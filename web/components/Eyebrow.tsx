export function Eyebrow({ children, dark = false }: { children: React.ReactNode; dark?: boolean }) {
  return <p className={`eyebrow mb-2.5 ${dark ? "eyebrow-on-dark" : ""}`}>{children}</p>;
}

export function PageHeader({ eyebrow, title, sub }: { eyebrow: string; title: string; sub?: string }) {
  return (
    <header className="mb-8">
      <Eyebrow>{eyebrow}</Eyebrow>
      <h1 className="text-[40px] leading-[1.1]">{title}</h1>
      {sub && <p className="mt-1.5 text-[19px] tracking-[-0.01em] text-muted-48">{sub}</p>}
    </header>
  );
}
