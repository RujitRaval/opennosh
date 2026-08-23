import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PublicFoodRecord } from "@/components/foods/public-food-record";
import { PublicBreadcrumbs } from "@/components/public/public-breadcrumbs";
import { isSupportedLanguage, routes } from "@/lib/routes";
import type { CatalogueFoodSource } from "@/lib/types";

const sources: readonly CatalogueFoodSource[] = ["usda", "community"];
const sourceIdPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const foodLocalePattern = /^[a-z]{2,3}(?:-[A-Z]{2})?$/;

export const metadata: Metadata = {
  title: "Food record - opennosh",
  description: "Inspect nutrition together with its source, version, license, and provenance.",
};

export default async function FoodRecordPage({
  params,
  searchParams,
}: {
  params: Promise<{ language: string; source: string; sourceId: string }>;
  searchParams: Promise<{ food_locale?: string | string[] }>;
}) {
  const { language, source, sourceId } = await params;
  const query = await searchParams;
  if (
    !isSupportedLanguage(language) ||
    !sources.includes(source as CatalogueFoodSource) ||
    !sourceIdPattern.test(sourceId)
  ) {
    notFound();
  }

  const requestedLocale = Array.isArray(query.food_locale)
    ? query.food_locale[0]
    : query.food_locale;
  const foodLocale = requestedLocale && foodLocalePattern.test(requestedLocale)
    ? requestedLocale
    : "global";

  return (
    <main id="main-content" className="food-record-page">
      <PublicBreadcrumbs
        items={[
          { label: "Home", href: routes.publicHome(language) },
          {
            label: "Explore",
            href: `${routes.publicHub("explore", language)}?${new URLSearchParams({ food_locale: foodLocale })}`,
          },
          { label: "Food record" },
        ]}
      />
      <PublicFoodRecord
        language={language}
        source={source as CatalogueFoodSource}
        sourceId={sourceId}
        foodLocale={foodLocale}
      />
    </main>
  );
}
