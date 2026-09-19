"use client";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return <main className="p-8 max-w-xl mx-auto"><h1>We couldn’t open this view.</h1><p className="muted my-6">Your source data hasn’t changed. Try loading the view again.</p><button className="button" onClick={reset}>Try again</button></main>;
}
