/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: '#1A2523', 2: '#3F4C4A' },
        mut: { DEFAULT: '#6B7B79', 2: '#94A3A1' },
        line: { DEFAULT: '#E3E8E7', 2: '#EDF1F0' },
        soft: '#F7FAFA',
        teal: {
          50: '#F0FDFA',
          100: '#CCFBF1',
          200: '#99F6E4',
          400: '#2DD4BF',
          500: '#14B8A6',
          600: '#0D9488',
          700: '#0F766E',
          800: '#115E59',
          900: '#134E4A',
        },
        pass: { DEFAULT: '#15803D', border: '#BBF7D0', bg: '#F0FDF4', deep: '#166534' },
        fail: { DEFAULT: '#DC2626', border: '#FECACA', bg: '#FEF2F2', deep: '#B91C1C' },
        pend: { DEFAULT: '#C2740A', border: '#FDE3AF', bg: '#FFFBEB', deep: '#92590A' },
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          'PingFang SC',
          'Hiragino Sans GB',
          'Microsoft YaHei',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      borderRadius: {
        card: '12px',
      },
      boxShadow: {
        card: '0 1px 2px rgba(16,32,30,.04)',
        pop: '0 12px 32px -8px rgba(16,32,30,.18), 0 0 0 1px rgba(16,32,30,.04)',
      },
      keyframes: {
        flash: {
          '0%': { backgroundColor: '#FFFBEB', borderColor: '#FDE3AF' },
          '55%': { backgroundColor: '#FEF3C7', borderColor: '#F5C77A' },
          '100%': { backgroundColor: '#FFFFFF', borderColor: '#E3E8E7' },
        },
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%,100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
      },
      animation: {
        flash: 'flash 600ms ease-out 1',
        'fade-in': 'fade-in 160ms ease-out 1',
        shimmer: 'shimmer 1.4s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
