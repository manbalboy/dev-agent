/**
 * 현재 대시보드는 Next 내부 API 라우트(/api/...)를 같은 origin에서 호출한다.
 * 필요 시 이 함수를 한 곳에서 수정해 베이스 URL 정책을 바꿀 수 있다.
 */
export function api(path: string) {
  return path;
}
