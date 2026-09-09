export const states: Record<string, [string, string]> = {
  NOT_DOWNLOADED: ["尚未下载", "default"],
  PENDING: ["待下载 / 复核", "default"],
  RUNNING: ["下载中", "blue"],
  WAITING: ["等待发布 / 重试", "gold"],
  BLOCKED: ["存在异常", "red"],
  UNVERIFIED: ["覆盖待核验", "gold"],
  RESPONSE_VALIDATED: ["响应已校验", "cyan"],
  VERIFIED: ["日线覆盖已验证", "green"],
  CLOSED: ["非交易日", "default"],
  NOT_APPLICABLE: ["合约不适用", "default"],
  VALIDATED: ["已校验并发布", "green"],
  SPLIT: ["已拆分", "default"],
};
export function scopeUrl(
  page: string,
  scope: { dataset: string; scope: string; start: string; end: string },
) {
  return `${page}?${new URLSearchParams(scope)}`;
}
