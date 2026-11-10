export const stages = [
  "applied",
  "interviewing",
  "offer",
  "declined",
  "rejected",
];
export function stageLabel(stage){return ({ready:'Ready',saved:'Saved',applied:'Applied',interviewing:'Interviewing',offer:'Offer',rejected:'Rejected by employer',declined:'Withdrawn / declined by me',deleted:'Deleted / blacklisted'})[stage]||stage;}
export const views = [
  "opportunities",
  "applications",
  "sources",
  "cvs",
  "settings",
];
export const emptyFilters = () => ({
  q: "",
  stage: "",
  arrangement: "",
  focus: "",
  region: "",
  eligibility: "verified",
  sort: "recent",
});
export const eligible = (j) => j._meta?.eligibility?.canApply === true;
export function regionOf(j) {
  const v = j._meta?.geography?.destination || j.region || "";
  if (/\b(united states|usa|us)\b/i.test(v)) return "us";
  if (/\b(united kingdom|uk|england)\b/i.test(v)) return "uk";
  if (
    /\b(ethiopia|addis ababa)\b/i.test(v) &&
    j._meta?.geography?.arrangement === "local"
  )
    return "ethiopia";
  if (/\b(africa|emea|worldwide|global)\b/i.test(v)) return "international";
  return "other";
}
export function publication(j) {
  const v = j._meta?.eligibility?.publishedAt;
  return v && Number.isFinite(Date.parse(v)) ? Date.parse(v) : 0;
}
export function selectJobs(jobs, view, f) {
  const q = f.q.trim().toLowerCase();
  return jobs
    .filter((j) => {
      if (
        j.status === "deleted" ||
        (view === "applications"
          ? !stages.includes(j.status)
          : !["ready", "saved"].includes(j.status))
      )
        return false;
      if (
        view === "opportunities" &&
        f.eligibility !== "all" &&
        (f.eligibility === "verified" ? !eligible(j) : eligible(j))
      )
        return false;
      return (
        (!q ||
          `${j.title} ${j.company} ${j.region} ${(j.tags || []).join(" ")}`
            .toLowerCase()
            .includes(q)) &&
        (!f.stage || j.status === f.stage) &&
        (!f.arrangement || j._meta?.geography?.arrangement === f.arrangement) &&
        (!f.region || regionOf(j) === f.region) &&
        (!f.focus ||
          (j.tags || []).some((t) => t.toLowerCase().includes(f.focus)))
      );
    })
    .sort((a, b) =>
      f.sort === "company"
        ? a.company.localeCompare(b.company)
        : f.sort === "action"
          ? (a.nextAction?.due || "9999").localeCompare(
              b.nextAction?.due || "9999",
            )
          : f.sort === "match"
            ? (b.matchScore || 0) - (a.matchScore || 0)
            : publication(b) - publication(a) ||
              a.company.localeCompare(b.company),
    );
}
export function readCache(raw) {
  try {
    const d = JSON.parse(raw);
    return d?.version === 2 &&
      Array.isArray(d.jobs) &&
      d.jobs.every(
        (j) =>
          j &&
          typeof j.id === "string" &&
          typeof j.title === "string" &&
          typeof j.company === "string" &&
          typeof j.status === "string",
      )
      ? d
      : null;
  } catch {
    return null;
  }
}
export function routeFrom(search) {
  const p = new URLSearchParams(search),
    filters = emptyFilters();
  for (const k of Object.keys(filters)) if (p.has(k)) filters[k] = p.get(k);
  return {
    view: views.includes(p.get("view")) ? p.get("view") : "opportunities",
    filters,
    job: p.get("job") || "",
  };
}
export function routeTo(view, f, job = "") {
  const p = new URLSearchParams({ view });
  for (const [k, v] of Object.entries(f))
    if (v && v !== emptyFilters()[k]) p.set(k, v);
  if (job) p.set("job", job);
  return `?${p}`;
}
export function safeLink(url) {
  try {
    const u = new URL(url, location.origin);
    return u.protocol === "https:" ||
      (u.origin === location.origin && u.pathname.startsWith("/cvs/"))
      ? u.href
      : "";
  } catch {
    return "";
  }
}
export function displayDate(v) {
  if (/^\d+\s+(?:hours?|days?)\s+ago$/i.test(v || '')) return `Source age: ${v}`;
  if (!v || !Number.isFinite(Date.parse(v))) return "Not recorded";
  const dayOnly = /^\d{4}-\d{2}-\d{2}$/.test(v);
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Africa/Addis_Ababa",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: dayOnly ? undefined : "2-digit",
    minute: dayOnly ? undefined : "2-digit",
  }).format(new Date(v));
}
