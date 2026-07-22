"""Vendor-facts registry — citation-required published vendor data."""

from specodex.vendors.registry import (
    VENDOR_DATA_DIR,
    FactKind,
    VendorFact,
    VendorProfile,
    VendorRegistryError,
    load_vendor_profiles,
    profile_for_manufacturer,
    slug_for_manufacturer,
)

__all__ = [
    "VENDOR_DATA_DIR",
    "FactKind",
    "VendorFact",
    "VendorProfile",
    "VendorRegistryError",
    "load_vendor_profiles",
    "profile_for_manufacturer",
    "slug_for_manufacturer",
]
