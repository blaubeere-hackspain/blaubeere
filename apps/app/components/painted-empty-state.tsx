import Image, { type StaticImageData } from "next/image";
import type { ReactNode } from "react";

export function PaintedEmptyState({ image, title, children }: { image: StaticImageData; title: string; children: ReactNode }) {
  return <div className="painted-empty-state">
    <div className="empty-painting"><Image src={image} alt="" sizes="(max-width: 700px) 90vw, 560px" placeholder="blur"/></div>
    <div className="empty-message"><h2>{title}</h2>{children}</div>
  </div>;
}
