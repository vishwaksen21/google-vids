import Link from "next/link";
import { LoaderCircle } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "tertiary" | "danger";
export type ButtonSize = "sm" | "md";

const base =
  "inline-flex items-center justify-center gap-2 rounded-md font-medium whitespace-nowrap transition-colors duration-150 " +
  "disabled:cursor-not-allowed aria-disabled:cursor-not-allowed select-none";

const variants: Record<ButtonVariant, string> = {
  primary:
    "bg-red text-white hover:bg-red-dark active:bg-red-deep disabled:bg-gray-300 disabled:text-white aria-disabled:bg-gray-300",
  secondary:
    "bg-white text-ink border border-gray-300 hover:border-red hover:text-red-dark active:bg-red-50 disabled:text-gray disabled:border-gray-200 disabled:bg-gray-50",
  tertiary: "bg-transparent text-red-dark hover:bg-red-50 active:bg-red-100 disabled:text-gray",
  danger:
    "bg-white text-red-dark border border-red hover:bg-red hover:text-white active:bg-red-dark disabled:text-gray disabled:border-gray-200 disabled:bg-gray-50",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-[13px]",
  md: "h-10 px-4 text-[14px]",
};

export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "md", extra = ""): string {
  return `${base} ${variants[variant]} ${sizes[size]} ${extra}`;
}

type ButtonProps = ComponentProps<"button"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
};

export function Button({ variant = "secondary", size = "md", loading = false, icon, className = "", children, disabled, type = "button", ...rest }: ButtonProps) {
  return (
    <button type={type} className={buttonClass(variant, size, className)} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {loading ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : icon}
      {children}
    </button>
  );
}

type LinkButtonProps = ComponentProps<typeof Link> & { variant?: ButtonVariant; size?: ButtonSize; icon?: ReactNode };

export function LinkButton({ variant = "secondary", size = "md", icon, className = "", children, ...rest }: LinkButtonProps) {
  return (
    <Link className={buttonClass(variant, size, className)} {...rest}>
      {icon}
      {children}
    </Link>
  );
}
