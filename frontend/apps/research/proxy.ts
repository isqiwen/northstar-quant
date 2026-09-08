import { pageSecurity } from "../../shared/page-security";
export const proxy = pageSecurity;
export const config = {
  matcher: ["/((?!api|_next/static|_next/image|health|favicon.ico).*)"],
};
