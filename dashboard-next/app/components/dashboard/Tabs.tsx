export type DashboardTab = 'jobs' | 'settings' | 'workflow';

type TabsProps = {
  tab: DashboardTab;
  onChange: (tab: DashboardTab) => void;
};

/**
 * 화면이 커질수록 탭 개수가 늘어날 수 있어, 탭 렌더링을 별도 컴포넌트로 분리한다.
 */
export function Tabs({ tab, onChange }: TabsProps) {
  return (
    <nav className="tabs">
      <button className={`tabBtn ${tab === 'jobs' ? 'active' : ''}`} onClick={() => onChange('jobs')}>
        리스트
      </button>
      <button className={`tabBtn ${tab === 'settings' ? 'active' : ''}`} onClick={() => onChange('settings')}>
        설정
      </button>
      <button className={`tabBtn ${tab === 'workflow' ? 'active' : ''}`} onClick={() => onChange('workflow')}>
        노드 편집
      </button>
    </nav>
  );
}
