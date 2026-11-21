export function Sigil({ size = 40 }: { size?: number }) {
  return (
    <span
      className="relative inline-flex shrink-0 items-center justify-center"
      style={{ width: size, height: size }}
      aria-hidden
    >
      <svg
        viewBox="0 0 48 48"
        width={size}
        height={size}
        className="absolute inset-0 animate-orbit opacity-70"
      >
        <circle
          cx="24"
          cy="24"
          r="21"
          fill="none"
          stroke="url(#sigil-grad)"
          strokeWidth="1"
          strokeDasharray="3 6"
        />
        <defs>
          <linearGradient id="sigil-grad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#a78bfa" />
            <stop offset="100%" stopColor="#f0b429" />
          </linearGradient>
        </defs>
      </svg>
      <svg viewBox="0 0 48 48" width={size} height={size} className="relative">
        <circle cx="24" cy="24" r="14" fill="rgba(167,139,250,0.08)" stroke="#a78bfa" strokeOpacity="0.4" />
        <path d="M24 9 L33 24 L24 39 L15 24 Z" fill="none" stroke="#a78bfa" strokeWidth="1.2" />
        <circle cx="24" cy="24" r="3.2" fill="#a78bfa" />
      </svg>
    </span>
  );
}
