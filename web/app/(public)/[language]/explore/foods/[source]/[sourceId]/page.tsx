import type { Metadata } from "next";
import { headers } from "next/headers";
import { notFound } from "next/navigation";

import { PublicFoodRecord } from "@/components/foods/public-food-record";
import { PublicBreadcrumbs } from "@/components/public/public-breadcrumbs";
import { loadPublicFoodRecord } from "@/lib/server/public-food-record";
import { isSupportedLanguage, routes } from "@/lib/routes";
import type { CatalogueFoodSource } from "@/lib/types";
import { getCatalog } from "@/lib/i18n/catalog";

const sources: readonly CatalogueFoodSource[] = ["usda", "community"];
const sourceIdPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const foodLocalePattern = /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ language: string }>;
}): Promise<Metadata> {
  const { language } = await params;
  if (!isSupportedLanguage(language)) return {};
  const copy = getCatalog(language).metadata;
  return { title: copy.foodTitle, description: copy.foodDescription };
}

export default async function FoodRecordPage({
  params,
  searchParams,
}: {
  params: Promise<{ language: string; source: string; sourceId: string }>;
  searchParams: Promise<{
    food_locale?: string | string[];
    portion?: string | string[];
    units?: string | string[];
  }>;
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
  const requestedPortion = Array.isArray(query.portion) ? query.portion[0] : query.portion;
  const initialPortionIndex = requestedPortion && /^[0-9]+$/.test(requestedPortion)
    ? Number(requestedPortion)
    : undefined;
  const requestedUnits = Array.isArray(query.units) ? query.units[0] : query.units;
  const initialMeasurement = requestedUnits === "us" ? "us" : "metric";
  const recordSource = source as CatalogueFoodSource;
  const copy = getCatalog(language);
  const requestHeaders = await headers();
  const initialState = await loadPublicFoodRecord({
    source: recordSource,
    sourceId,
    foodLocale,
    clientAddress: requestHeaders.get("x-forwarded-for"),
  });
  if (initialState.kind === "not-found") notFound();

  return (
    <main id="main-content" className="food-record-page">
      <PublicBreadcrumbs
        label={copy.common.breadcrumb}
        items={[
          { label: copy.common.home, href: routes.publicHome(language) },
          {
            label: copy.common.explore,
            href: `${routes.publicHub("explore", language)}?${new URLSearchParams({ food_locale: foodLocale })}`,
          },
          { label: copy.common.foodRecord },
        ]}
      />
      <PublicFoodRecord
        key={`${recordSource}:${sourceId}:${foodLocale}`}
        initialState={initialState}
        language={language}
        source={recordSource}
        sourceId={sourceId}
        foodLocale={foodLocale}
        initialPortionIndex={initialPortionIndex}
        initialMeasurement={initialMeasurement}
      />
    </main>
  );
}
