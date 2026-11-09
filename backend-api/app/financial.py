"""Validated financial table/Excel intake using the existing searchable bulk storage."""

from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import asyncpg
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.agents.state import UnifiedVaultExtraction
from app.bulk import BATCH_SIZE, MAX_BULK_ROWS, BulkImporter, BulkReport, ValidRow
from app.parsers import FileKind, FileParseError, detect_file_kind
from app.repositories import FamilyMember, MemberRepo
from app.security import Principal

CURRENCIES = (
    "SGD", "USD", "INR", "EUR", "GBP", "AUD", "CAD", "MYR", "JPY", "CNY",
    "HKD", "CHF", "AED", "NZD", "THB", "IDR", "PHP", "KRW", "TWD", "SAR",
)
VALID_CURRENCIES = set(CURRENCIES) | set(
    "AFN ALL AMD ANG AOA ARS AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD "
    "BTN BWP BYN BZD CDF CLP COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB FJD "
    "FKP GEL GHS GIP GMD GNF GTQ GYD HNL HTG HUF ILS IQD IRR ISK JMD JOD KES KGS "
    "KHR KMF KPW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP "
    "MRU MUR MVR MWK MXN MZN NAD NGN NIO NOK NPR OMR PAB PEN PGK PKR PLN PYG QAR "
    "RON RSD RUB RWF SBD SCR SDG SEK SHP SLE SOS SRD SSP STN SVC SYP SZL TJS TMT "
    "TND TOP TRY TTD TZS UAH UGX UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR "
    "ZMW ZWG".split()
)
HEADERS = ("Name of the person", "Category", "Subcategory", "Amount", "Currency", "Details")
DATE_HEADERS = ("Transaction date", "Payment due date", "Paid")


class FinancialRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    person_name: str = Field(min_length=1, max_length=80)
    category: Literal["income", "expense"]
    subcategory: str = Field(min_length=1, max_length=80)
    amount: Decimal = Field(gt=0, lt=Decimal("1000000000000"), decimal_places=2)
    currency: str
    details: str = Field(min_length=1, max_length=2000)
    transaction_date: date | None = None
    payment_due_date: date | None = None
    is_paid: bool = True

    @field_validator("category", "currency", mode="before")
    @classmethod
    def normalize_choice(cls, value: Any) -> Any:
        if isinstance(value, str):
            trimmed = value.strip()
            return trimmed.upper() if trimmed.upper() in VALID_CURRENCIES else trimmed.lower()
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        if value not in VALID_CURRENCIES:
            raise ValueError("Choose a supported currency code")
        return value

    @field_validator("transaction_date", "payment_due_date", mode="before")
    @classmethod
    def normalize_excel_date(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        return value

    @model_validator(mode="after")
    def check_bill(self) -> FinancialRow:
        if not self.is_paid and (self.category != "expense" or self.payment_due_date is None):
            raise ValueError("Unpaid bills must be expenses with a payment due date")
        return self


class FinancialTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[FinancialRow] = Field(min_length=1, max_length=MAX_BULK_ROWS)


def read_excel(data: bytes) -> list[tuple[int, FinancialRow | str]]:
    if detect_file_kind(data) is not FileKind.XLSX:
        raise FileParseError("Choose an Excel .xlsx workbook")
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except (ValueError, OSError, KeyError, BadZipFile, InvalidFileException, ParseError) as exc:
        raise FileParseError("Excel workbook could not be read") from exc
    try:
        if "Entries" not in workbook.sheetnames:
            raise FileParseError("Workbook must contain the Entries sheet from the sample template")
        sheet = workbook["Entries"]
        if sheet.max_row > MAX_BULK_ROWS + 1 or sheet.max_column not in (6, 9):
            raise FileParseError(
                f"Entries must have six or nine columns and at most {MAX_BULK_ROWS} rows",
            )
        iterator = sheet.iter_rows()
        header = next(iterator, ())
        expected = HEADERS + DATE_HEADERS if len(header) == 9 else HEADERS
        if tuple(str(cell.value or "").strip().casefold() for cell in header) != tuple(
            name.casefold() for name in expected
        ):
            raise FileParseError("Excel headers must be: " + ", ".join(expected))
        rows: list[tuple[int, FinancialRow | str]] = []
        keys: tuple[str, ...] = (
            "person_name", "category", "subcategory", "amount", "currency", "details",
        )
        if len(header) == 9:
            keys += ("transaction_date", "payment_due_date", "is_paid")
        for number, cells in enumerate(iterator, start=2):
            if all(cell.value is None or str(cell.value).strip() == "" for cell in cells):
                continue
            if any(cell.data_type == "f" for cell in cells):
                rows.append((number, "Formulas are not accepted; paste values only"))
                continue
            values = dict(zip(keys, (cell.value for cell in cells), strict=True))
            if values["amount"] is not None:
                values["amount"] = str(values["amount"])
            if "is_paid" in values and values["is_paid"] is None:
                values["is_paid"] = True
            try:
                rows.append((number, FinancialRow.model_validate(values)))
            except ValidationError as exc:
                errors = "; ".join(
                    f"{error['loc'][0] if error['loc'] else 'row'}: {error['msg']}"
                    for error in exc.errors()
                )
                rows.append((number, errors))
        if not rows:
            raise FileParseError("Entries sheet has no financial data")
        return rows
    finally:
        workbook.close()


def financial_extraction(row: FinancialRow, person_name: str) -> UnifiedVaultExtraction:
    text = (
        f"Person: {person_name}\nCategory: {row.category}\n"
        f"Subcategory: {row.subcategory}\nAmount: {row.amount:.2f}\n"
        f"Currency: {row.currency}\nDetails: {row.details}\nPaid: {row.is_paid}"
    )
    if row.transaction_date:
        text += f"\nTransaction date: {row.transaction_date}"
    if row.payment_due_date:
        text += f"\nPayment due date: {row.payment_due_date}"
    return UnifiedVaultExtraction(
        category=row.category, document_name=f"{person_name} - {row.subcategory}"[:200],
        requires_alert=False, extracted_text=text, financial_value=float(row.amount),
        confidence_score=1,
    )


async def import_financial_rows(
    rows: list[tuple[int, FinancialRow | str]], principal: Principal,
    pool: asyncpg.Pool, importer: BulkImporter,
) -> BulkReport:
    members = await MemberRepo(pool).list_all(principal.family_id)
    report = BulkReport()
    valid: list[ValidRow] = []
    for number, row in rows:
        if isinstance(row, str):
            report.reject(number, row)
            continue
        matches = [member for member in members
                   if member.display_name.casefold() == row.person_name.casefold()]
        if len(matches) != 1:
            report.reject(number, "Person must uniquely match an available family member name")
            continue
        member = matches[0]
        if not principal.is_admin and member.id != principal.member_id:
            report.reject(number, "Not allowed to upload financial data for this person")
            continue
        extraction = financial_extraction(row, member.display_name)
        valid.append(ValidRow(
            row=number, member_id=member.id, extraction=extraction,
            currency=row.currency, subcategory=row.subcategory,
            details=row.details, amount=row.amount,
            transaction_date=row.transaction_date, payment_due_date=row.payment_due_date,
            is_paid=row.is_paid,
        ))
    for start in range(0, len(valid), BATCH_SIZE):
        if not await importer.import_rows(valid[start:start + BATCH_SIZE], principal, report):
            for skipped in valid[start + BATCH_SIZE:]:
                report.reject(skipped.row, "Import stopped; this row was not saved")
            break
    return report


def build_excel_template(
    members: list[FamilyMember], currency: str,
    subcategories: dict[str, list[str]] | None = None,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Entries"
    sheet.append(HEADERS + DATE_HEADERS)
    name = members[0].display_name if members else "Your exact family member name"
    for values in (
        (name, "income", "Salary", 5000, currency, "Example: monthly salary"),
        (name, "expense", "Groceries", 125.50, currency, "Example: household groceries"),
    ):
        sheet.append((*values, None, None, True))
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="742B3C")
        cell.alignment = Alignment(vertical="center")
    for col, width in zip("ABCDEFGHI", (30, 16, 24, 18, 14, 55, 22, 22, 12), strict=True):
        sheet.column_dimensions[col].width = width
    sheet.freeze_panes = "A2"
    sheet.row_dimensions[1].height = 26
    for row_number in (2, 3):
        sheet[f"D{row_number}"].number_format = "0.00"
    table = Table(displayName="FinancialEntries", ref="A1:I3")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    sheet.add_table(table)
    lists = workbook.create_sheet("Lists")
    subcategories = subcategories or {
        "income": ["Salary", "Bonus", "Interest"],
        "expense": ["Groceries", "Rent", "Utilities"],
    }
    currencies = sorted(VALID_CURRENCIES)
    lists.append((
        "People", "Currencies", "Categories", "Income subcategories", "Expense subcategories",
    ))
    for index in range(max(len(members), len(currencies),
                           *(len(values) for values in subcategories.values()))):
        lists.append((
            members[index].display_name if index < len(members) else None,
            currencies[index] if index < len(currencies) else None,
            ("income", "expense")[index] if index < 2 else None,
            subcategories["income"][index] if index < len(subcategories["income"]) else None,
            subcategories["expense"][index] if index < len(subcategories["expense"]) else None,
        ))
    for row in lists:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    lists.sheet_state = "hidden"
    for label, col, length, target in (
        ("People", "A", len(members), "A"),
        ("Currencies", "B", len(currencies), "E"),
        ("Categories", "C", 2, "B"),
    ):
        if length == 0:
            continue
        workbook.defined_names.add(DefinedName(
            label, attr_text=f"'Lists'!${col}$2:${col}${length + 1}",
        ))
        validation = DataValidation(type="list", formula1=f"={label}", allow_blank=False)
        validation.errorTitle = "Choose an available value"
        validation.error = "Use the dropdown list."
        validation.showErrorMessage = True
        sheet.add_data_validation(validation)
        validation.add(f"{target}2:{target}{MAX_BULK_ROWS + 1}")
    for category, col in (("income", "D"), ("expense", "E")):
        label = f"{category.title()}Subcategories"
        workbook.defined_names.add(DefinedName(
            label, attr_text=f"'Lists'!${col}$2:${col}${len(subcategories[category]) + 1}",
        ))
    validation = DataValidation(
        type="list",
        formula1='INDIRECT(IF(B2="income","IncomeSubcategories","ExpenseSubcategories"))',
        allow_blank=False,
    )
    validation.showErrorMessage = False  # Suggestions allow new subcategory values.
    sheet.add_data_validation(validation)
    validation.add(f"C2:C{MAX_BULK_ROWS + 1}")
    paid = DataValidation(type="list", formula1='"TRUE,FALSE"', allow_blank=True)
    sheet.add_data_validation(paid)
    paid.add(f"I2:I{MAX_BULK_ROWS + 1}")
    instructions = workbook.create_sheet("Instructions")
    for line in (
        "Replace or delete the two example rows before uploading.",
        "Keep the Entries sheet and its nine column headers unchanged.",
        "Name of the person must exactly match a unique family member in the People dropdown.",
        "Category: income or expense. Subcategory: salary, groceries, rent, etc.",
        "Amount: positive number, maximum two decimal places. Currency is required.",
        "Details: explain the transaction (up to 2,000 characters). "
        "No formulas; paste values only.",
        f"Maximum {MAX_BULK_ROWS} data rows and 5 MB. "
        "Valid rows import; errors report row numbers.",
        "Amounts are stored in the selected currency. No currency conversion is performed.",
        "Uploading the same data again creates new records; import each transaction only once.",
        "Transaction date: YYYY-MM-DD. Leave blank only for undated records.",
        "For upcoming bills: Category expense, Payment due date YYYY-MM-DD, Paid FALSE.",
        "Paid TRUE means settled. Past/current monthly totals include paid, dated entries only.",
    ):
        instructions.append((line,))
    instructions.column_dimensions["A"].width = 110
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
