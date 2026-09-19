import { landingOrigin } from "../../metadata";

export default function sitemap() {
  return [{ url: new URL("/", landingOrigin).href }];
}
