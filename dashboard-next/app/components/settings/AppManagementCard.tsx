import type { AppItem } from '../../types/dashboard';

type Props = {
  apps: AppItem[];
  workflowOptions: Array<{ workflow_id: string; name: string }>;
  appCode: string;
  appName: string;
  appMsg: string;
  setAppCode: (v: string) => void;
  setAppName: (v: string) => void;
  onSave: () => void;
  onRemove: (code: string) => void;
  onMapWorkflow: (code: string, workflowId: string) => void;
};

/**
 * 앱 등록/삭제 관리 영역.
 * 리스트를 테이블로 유지해 모바일/데스크톱에서 동일한 데이터 문맥을 제공한다.
 */
export function AppManagementCard(props: Props) {
  const { apps, workflowOptions, appCode, appName, appMsg, setAppCode, setAppName, onSave, onRemove, onMapWorkflow } = props;

  return (
    <article className="box">
      <h2>앱 관리</h2>
      <div className="row">
        <input className="input" placeholder="앱 코드 (mvp-1)" value={appCode} onChange={(e) => setAppCode(e.target.value)} />
        <input className="input" placeholder="앱 이름" value={appName} onChange={(e) => setAppName(e.target.value)} />
        <button className="btn" onClick={onSave}>저장</button>
      </div>
      <p className="hint">{appMsg}</p>
      <div className="tableWrap">
        <table>
          <thead>
            <tr>
              <th>앱명</th>
              <th>코드</th>
              <th>저장소</th>
              <th>워크플로우 매핑</th>
              <th>작업</th>
            </tr>
          </thead>
          <tbody>
            {apps.map((app) => (
              <tr key={app.code}>
                <td>{app.name}</td>
                <td>{app.code}</td>
                <td>{app.repository}</td>
                <td>
                  <select
                    className="select"
                    value={app.workflow_id ?? workflowOptions[0]?.workflow_id ?? ''}
                    disabled={workflowOptions.length === 0}
                    onChange={(event) => onMapWorkflow(app.code, event.target.value)}
                  >
                    {workflowOptions.length === 0 ? <option value="">워크플로우 없음</option> : null}
                    {workflowOptions.map((workflow) => (
                      <option key={workflow.workflow_id} value={workflow.workflow_id}>
                        {workflow.name} ({workflow.workflow_id})
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  {app.code === 'default' ? '-' : <button className="btn btnDanger" onClick={() => onRemove(app.code)}>삭제</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}
