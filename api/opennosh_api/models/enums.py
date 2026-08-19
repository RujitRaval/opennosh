from enum import StrEnum


class FoodSourceTable(StrEnum):
    REFERENCE = "foods_reference"
    COMMUNITY = "foods_community"
    ODBL = "foods_odbl"
    CUSTOM = "foods_custom"


class LoadUnit(StrEnum):
    KG = "kg"
    LB = "lb"
    BODYWEIGHT = "bodyweight"
    BAND = "band"
    MACHINE_UNITS = "machine_units"
    RPE_ONLY = "rpe_only"


class Provenance(StrEnum):
    LAB_ANALYSIS = "lab_analysis"
    GOVERNMENT_DATABASE = "government_database"
    MANUFACTURER_LABEL = "manufacturer_label"
    PUBLISHED_RECIPE_CALCULATION = "published_recipe_calculation"
    OWN_MEASUREMENT = "own_measurement"
