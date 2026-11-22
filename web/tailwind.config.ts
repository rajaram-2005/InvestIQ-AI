import type { Config } from 'tailwindcss';
export default { content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'], theme: { extend: { colors: { ink:'#222d2b', forest:'#214b40', sage:'#ecf2ed', canvas:'#f6f7f3', line:'#e5eae5' } } }, plugins: [] } satisfies Config;
