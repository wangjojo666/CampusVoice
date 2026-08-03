export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: Readonly<{
  eyebrow?: string;
  title: string;
  description: string;
  actions?: React.ReactNode;
}>) {
  return (
    <header className="mb-7 flex min-w-0 flex-col justify-between gap-4 sm:flex-row sm:items-end">
      <div className="min-w-0 max-w-3xl">
        {eyebrow ? (
          <p className="mb-2 text-xs font-bold tracking-[0.14em] text-teal-600 uppercase">
            {eyebrow}
          </p>
        ) : null}
        <h1 className="text-2xl font-extrabold tracking-[-0.035em] text-ink-950 sm:text-[2rem]">
          {title}
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-600 sm:text-[0.95rem]">
          {description}
        </p>
      </div>
      {actions ? (
        <div className="flex w-full min-w-0 flex-wrap gap-2 sm:w-auto sm:shrink-0">{actions}</div>
      ) : null}
    </header>
  );
}
