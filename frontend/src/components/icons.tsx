const P = { fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round" } as const;
const S = ({ size = 18, children }: { size?: number; children: React.ReactNode }) => <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" {...P}>{children}</svg>;

export const Mark = () => <S size={22}><circle cx="12" cy="4.5" r="1.8" /><path d="M12 6.5v11M5 13a7 7 0 0 0 14 0M8.5 10h7" /></S>;
export const NewsIcon = () => <S><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M8 9h8M8 12h8M8 15h5" /></S>;
export const SavedIcon = () => <S><path d="M7 4h10v16l-5-3.5L7 20z" /></S>;
export const SourcesIcon = () => <S><circle cx="12" cy="12" r="8" /><path d="M4 12h16M12 4c2.5 2.5 2.5 13.5 0 16M12 4c-2.5 2.5-2.5 13.5 0 16" /></S>;
export const SettingsIcon = () => <S><circle cx="12" cy="12" r="3" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" /></S>;
export const SearchIcon = () => <S size={16}><circle cx="11" cy="11" r="6" /><path d="M16 16l4 4" /></S>;
export const InfoIcon = () => <S size={14}><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5" /><circle cx="12" cy="8" r=".6" fill="currentColor" /></S>;
export const ChevronDown = () => <S size={14}><path d="M6 9l6 6 6-6" /></S>;
export const Chevron = () => <S size={16}><path d="M9 6l6 6-6 6" /></S>;
export const Back = () => <S size={14}><path d="M15 6l-6 6 6 6" /></S>;
export const Bookmark = ({ on }: { on?: boolean }) => <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" {...P} fill={on ? "currentColor" : "none"}><path d="M7 4h10v16l-5-3.5L7 20z" /></svg>;
export const Star = ({ on }: { on?: boolean }) => <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" {...P} fill={on ? "currentColor" : "none"}><path d="M12 4l2.4 5 5.4.7-4 3.8 1 5.4L12 16.2 7.2 18.9l1-5.4-4-3.8 5.4-.7z" /></svg>;
export const Check = () => <S size={14}><path d="M5 12.5l4.5 4.5L19 7" /></S>;
export const ExploreIcon = () => <S><path d="M4 19V9M10 19V5M16 19v-7M21 19H3" /></S>;
export const StatusIcon = () => <S><path d="M3 12h4l2.5-6 4 12 2.5-6H21" /></S>;
