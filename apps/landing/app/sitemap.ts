import { landingOrigin } from "../../metadata";

export default function sitemap() {
  return ["/", "/pricing"].map(path => ({ url: new URL(path, landingOrigin).href }));
}
