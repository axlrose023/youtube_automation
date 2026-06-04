export function formatDate(value?: string | null) {
  if (!value) {
    return "—";
  }
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

export function formatPercent(value: number) {
  return `${Math.round(value)}%`;
}

export function formatMinutes(value?: number | null) {
  if (value == null) {
    return "—";
  }
  return `${value.toFixed(1)} min`;
}

export function formatBytes(value?: number | null) {
  if (value == null) {
    return "—";
  }

  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unitIndex = 0;

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }

  const digits = unitIndex >= 2 ? 1 : 0;
  return `${size.toFixed(digits)} ${units[unitIndex]}`;
}
