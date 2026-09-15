const exchanges: Record<string, string> = {
  SHFE: "上期所",
  INE: "上海能源",
  DCE: "大商所",
  CZCE: "郑商所",
  CFFEX: "中金所",
  GFEX: "广期所",
};
export function exchangeName(code: string) {
  return exchanges[code] || code;
}
