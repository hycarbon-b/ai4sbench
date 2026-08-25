import type { HTMLAttributes } from "react";

import { cn } from "../../lib/utils";

function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("inline-flex items-center rounded-sm border border-sky-400/30 bg-sky-400/10 px-1.5 py-0.5 text-[11px] font-medium text-sky-200", className)} {...props} />;
}

export { Badge };
