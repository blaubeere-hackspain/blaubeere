export const landingOrigin = process.env.NEXT_PUBLIC_LANDING_URL ?? "http://localhost:3102";
export const appOrigin = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3100";

export function pageMetadata(origin: string, path: string, title: string, description: string) {
  const url = new URL(path, origin).href;
  const image = {
    url: new URL("/social-preview.png", landingOrigin).href,
    width: 1734, height: 907,
    alt: "blau — Know your cash. Plan what’s next. Cash forecasts and what-if plans for the people behind the numbers, over an oil-painted landscape and financial ledger.",
  };
  return {
    metadataBase: new URL(origin), applicationName: "blau", title, description,
    alternates: { canonical: url },
    openGraph: { type: "website" as const, siteName: "blau", locale: "en_US", title, description, url, images: [image] },
    twitter: { card: "summary_large_image" as const, title, description, images: [image] },
  };
}
