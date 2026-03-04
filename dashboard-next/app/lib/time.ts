/**
 * 서버/DB 기준 UTC 시각 문자열을 한국 시간(Asia/Seoul) 포맷으로 보여준다.
 * 파싱이 불가능한 값은 원본을 그대로 반환해 데이터 손실을 막는다.
 */
export function kst(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}
