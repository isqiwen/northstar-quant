"use client";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <section>
      <h2>页面暂时无法显示</h2>
      <p>请检查服务状态；已提交的操作不会自动重发。</p>
      <button onClick={reset}>重新加载页面</button>
    </section>
  );
}
