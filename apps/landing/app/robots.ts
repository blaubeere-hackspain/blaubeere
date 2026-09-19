import { landingOrigin } from "../../metadata";

export default function robots() {
  return { rules: { userAgent: "*", allow: "/" }, sitemap: new URL("/sitemap.xml", landingOrigin).href };
}
