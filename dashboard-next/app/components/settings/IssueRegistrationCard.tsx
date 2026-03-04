import type { AppItem } from '../../types/dashboard';

type Props = {
  apps: AppItem[];
  issueApp: string;
  issueTrack: string;
  issueTitle: string;
  issueBody: string;
  issueMsg: string;
  setIssueApp: (v: string) => void;
  setIssueTrack: (v: string) => void;
  setIssueTitle: (v: string) => void;
  setIssueBody: (v: string) => void;
  onRegister: () => void;
};

/**
 * 작업 이슈 등록 폼.
 * 앱/트랙/제목/설명을 입력받아 상위 컴포넌트의 등록 액션을 호출한다.
 */
export function IssueRegistrationCard(props: Props) {
  const {
    apps,
    issueApp,
    issueTrack,
    issueTitle,
    issueBody,
    issueMsg,
    setIssueApp,
    setIssueTrack,
    setIssueTitle,
    setIssueBody,
    onRegister,
  } = props;

  return (
    <article className="box">
      <h2>작업 이슈 등록</h2>
      <div className="row">
        <select className="select" value={issueApp} onChange={(e) => setIssueApp(e.target.value)}>
          {apps.map((app) => (
            <option key={app.code} value={app.code}>{app.name} ({app.code})</option>
          ))}
        </select>
        <select className="select" value={issueTrack} onChange={(e) => setIssueTrack(e.target.value)}>
          <option value="new">new</option>
          <option value="enhance">enhance</option>
          <option value="bug">bug</option>
        </select>
      </div>
      <input className="input" placeholder="제목" value={issueTitle} onChange={(e) => setIssueTitle(e.target.value)} />
      <textarea className="textarea" placeholder="설명" value={issueBody} onChange={(e) => setIssueBody(e.target.value)} />
      <div className="row">
        <button className="btn" onClick={onRegister}>작업 이슈 등록</button>
        <p className="hint">{issueMsg}</p>
      </div>
    </article>
  );
}
