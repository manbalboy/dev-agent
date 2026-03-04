import { Moon, Sun } from 'lucide-react';

type HeaderProps = {
  title: string;
  subtitle: string;
  theme: 'dark' | 'light';
  onToggleTheme: () => void;
};

/**
 * 대시보드 상단 공통 헤더.
 * 타이틀과 현재 프런트 API 방식(Next Proxy)을 고정 배지로 보여준다.
 */
export function Header({ title, subtitle, theme, onToggleTheme }: HeaderProps) {
  return (
    <header className="header">
      <div>
        <h1>{title}</h1>
        <p className="hint">{subtitle}</p>
      </div>
      <div className="headerActions">
        <button className="btn themeToggle" onClick={onToggleTheme} title="테마 전환">
          <span className={`themeMark ${theme === 'light' ? 'active' : ''}`}>
            <Sun size={14} />
          </span>
          <span className={`themeMark ${theme === 'dark' ? 'active' : ''}`}>
            <Moon size={14} />
          </span>
        </button>
        <span className="badge">API: Next Proxy</span>
      </div>
    </header>
  );
}
