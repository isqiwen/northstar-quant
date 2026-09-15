import { Explorer } from "../../exploration/workspace";
import { Compactions } from "../../exploration/compactions";
export default function Page() {
  return (
    <>
      <Compactions />
      <Explorer mode="versions" />
    </>
  );
}
