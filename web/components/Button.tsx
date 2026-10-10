type Variant = "primary" | "ghost" | "dark" | "danger";

export function Button({ variant = "primary", small = false, className = "", ...props }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; small?: boolean }) {
  const v = variant === "primary" ? "" : `btn-${variant}`;
  return <button {...props} className={`btn ${v} ${small ? "!px-4 !py-2 !text-sm" : ""} ${className}`} />;
}
