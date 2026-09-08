export const dynamic = "force-dynamic";
import type { ReactNode } from "react";
import Workspace from "../workspace";
import "../../../shared/theme.css";
export const metadata = { title: "Northstar research" };
export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <Workspace>{children}</Workspace>
      </body>
    </html>
  );
}
