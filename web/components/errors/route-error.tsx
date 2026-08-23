"use client";

import { ApiProblem } from "@/lib/api/domain/problem";

import styles from "./route-error.module.css";

export function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const reference =
    error instanceof ApiProblem && error.reference !== "unavailable"
      ? error.reference
      : error.digest;

  return (
    <main className={styles.main} id="main-content">
      <section
        aria-labelledby="route-error-title"
        className={styles.panel}
        role="alert"
      >
        <p className={styles.eyebrow}>Something interrupted this page</p>
        <h1 id="route-error-title">The page could not finish loading.</h1>
        <p>{error instanceof ApiProblem ? error.message : "Please try the page again."}</p>
        {reference ? (
          <p className={styles.reference}>
            <small>Reference: {reference}</small>
          </p>
        ) : null}
        <button type="button" onClick={reset}>Try again</button>
      </section>
    </main>
  );
}
