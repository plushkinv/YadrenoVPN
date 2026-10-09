import type { ButtonHTMLAttributes, ReactNode } from 'react';
export function Button({ children, tone = 'primary', className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: 'primary' | 'secondary' | 'quiet'; children?: ReactNode }) {
  return <button type="button" className={`button button--${tone} ${className}`} {...props}>{children}</button>;
}
