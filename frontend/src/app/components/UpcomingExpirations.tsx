import type { UpcomingExpiration } from "@/lib/types";

export const URGENT_DAYS = 7;

export function daysLabel(days: number): string {
  if (days <= 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}

export function UpcomingExpirations({
  items, yearly = false,
}: { items: UpcomingExpiration[]; yearly?: boolean }) {
  return (
    <section
      aria-label={yearly ? "Expiring in the next 1 year" : "Upcoming expirations"}
      className="enterprise-panel"
    >
      <h2 className="section-title">
        {yearly ? "Expiring in the next 1 year" : "Expiring in the next 30 days"}
      </h2>
      {yearly ? <p className="mt-1 text-xs text-slate-500">From 30 days to less than 365 days away.</p> : null}
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">
          {yearly ? "Nothing expires from 30 days to less than a year." : "Nothing expires in the next 30 days."}
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((item) => {
            const urgent = item.days_remaining <= URGENT_DAYS;
            return (
              <li
                key={item.document_id}
                data-urgent={urgent}
                className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${
                  urgent ? "border-red-200 bg-[#fee2e2]" : "border-amber-200 bg-amber-50"
                }`}
              >
                <span className="min-w-0 break-words">
                  <span className="font-medium">{item.document_name}</span>
                  <span className="block text-xs text-slate-700">
                    {item.member_name} · {item.category}
                  </span>
                </span>
                <span className="shrink-0 text-right text-xs">
                  <span className={`block font-semibold ${urgent ? "text-red-700" : "text-amber-800"}`}>
                    {daysLabel(item.days_remaining)}
                  </span>
                  <span className="text-slate-700">{item.expiration_date}</span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
