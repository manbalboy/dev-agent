import type { WorkflowDefinition, WorkflowsResponse } from '../../types/dashboard';

type Props = {
  workflows: WorkflowsResponse | null;
  workflowMsg: string;
  onOpenWorkflow: (workflow: WorkflowDefinition) => void;
};

/**
 * 서버에 등록된 워크플로우 목록을 조회 전용으로 표시한다.
 */
export function WorkflowCatalogCard({ workflows, workflowMsg, onOpenWorkflow }: Props) {
  return (
    <article className="box">
      <h2>워크플로우(조회)</h2>
      <p className="hint">기본 워크플로우: {workflows?.default_workflow_id ?? '-'}</p>
      <p className="hint">행 더블클릭 시 노드 편집 탭에서 구조를 바로 확인할 수 있습니다.</p>
      {workflowMsg ? <p className="hint">{workflowMsg}</p> : null}
      <div className="tableWrap">
        <table>
          <thead>
            <tr>
              <th>workflow_id</th>
              <th>name</th>
              <th>version</th>
              <th>description</th>
              <th>열기</th>
            </tr>
          </thead>
          <tbody>
            {(workflows?.workflows ?? []).map((wf) => (
              <tr key={wf.workflow_id} onDoubleClick={() => onOpenWorkflow(wf)} title="더블클릭: 노드 편집에서 열기">
                <td>{wf.workflow_id}</td>
                <td>{wf.name}</td>
                <td>{wf.version}</td>
                <td>{wf.description ?? '-'}</td>
                <td>
                  <button className="btn" onClick={() => onOpenWorkflow(wf)}>
                    노드 편집으로 열기
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}
