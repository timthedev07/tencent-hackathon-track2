'use client';

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface GlassPanelProps {
  children: ReactNode;
  className?: string;
  outerClassName?: string;
  intensity?: 'soft' | 'strong';
  interactive?: boolean;
}

export function GlassPanel({
  children,
  className,
  outerClassName,
  interactive = false,
}: GlassPanelProps) {
  return (
    <section className={cn('panel', interactive && 'panel-interactive', outerClassName, className)}>
      {children}
    </section>
  );
}
