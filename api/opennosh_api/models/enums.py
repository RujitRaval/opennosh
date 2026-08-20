from enum import StrEnum


class FoodSourceTable(StrEnum):
    REFERENCE = "foods_reference"
    COMMUNITY = "foods_community"
    ODBL = "foods_odbl"
    CUSTOM = "foods_custom"
    RECIPE = "recipes"


class LoadUnit(StrEnum):
    KG = "kg"
    LB = "lb"
    BODYWEIGHT = "bodyweight"
    BAND = "band"
    MACHINE_UNITS = "machine_units"
    RPE_ONLY = "rpe_only"


class TargetDayType(StrEnum):
    TRAINING = "training"
    REST = "rest"


class BodyMetricType(StrEnum):
    BODY_WEIGHT = "body_weight"
    BODY_FAT_PERCENTAGE = "body_fat_percentage"
    HEIGHT = "height"
    WAIST_CIRCUMFERENCE = "waist_circumference"
    HIP_CIRCUMFERENCE = "hip_circumference"
    CHEST_CIRCUMFERENCE = "chest_circumference"
    NECK_CIRCUMFERENCE = "neck_circumference"
    UPPER_ARM_CIRCUMFERENCE = "upper_arm_circumference"
    THIGH_CIRCUMFERENCE = "thigh_circumference"


class BodyMetricUnit(StrEnum):
    KILOGRAM = "kg"
    POUND = "lb"
    PERCENT = "percent"
    CENTIMETER = "cm"
    INCH = "in"


class Provenance(StrEnum):
    LAB_ANALYSIS = "lab_analysis"
    GOVERNMENT_DATABASE = "government_database"
    MANUFACTURER_LABEL = "manufacturer_label"
    PUBLISHED_RECIPE_CALCULATION = "published_recipe_calculation"
    OWN_MEASUREMENT = "own_measurement"
