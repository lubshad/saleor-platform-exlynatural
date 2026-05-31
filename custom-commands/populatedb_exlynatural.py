from dataclasses import dataclass, field
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone
from django.utils.text import slugify

from saleor.account.models import Address
from saleor.channel.models import Channel
from saleor.menu.models import Menu, MenuItem
from saleor.product import ProductTypeKind
from saleor.product.models import (
    Category,
    Collection,
    CollectionChannelListing,
    Product,
    ProductChannelListing,
    ProductMedia,
    ProductType,
    ProductVariant,
    ProductVariantChannelListing,
)
from saleor.shipping import ShippingMethodType
from saleor.shipping.models import (
    ShippingMethod,
    ShippingMethodChannelListing,
    ShippingZone,
)
from saleor.warehouse.models import Stock, Warehouse


DEFAULT_CHANNEL_SLUG = "default-channel"
DEFAULT_COUNTRY = "IN"
DEFAULT_CURRENCY = "INR"
DEFAULT_PRICE = Decimal("299.00")
DEFAULT_SHIPPING_PRICE = Decimal("99.00")
DEFAULT_STOCK = 25
DEFAULT_ADMIN_EMAIL = "admin@example.com"
DEFAULT_ADMIN_PASSWORD = "admin"
SEEDER_NAME = "populatedb_exlynatural"
APP_DIR = Path(__file__).resolve().parents[4]
PRODUCT_IMAGE_DIR = APP_DIR / "seed-assets" / "exlynatural" / "products"
COLLECTION_IMAGE_DIR = APP_DIR / "seed-assets" / "exlynatural" / "collections"
CATEGORY_IMAGE_DIR = APP_DIR / "seed-assets" / "exlynatural" / "categories"
NAVBAR_MENU_SLUG = "navbar"

CATEGORY_IMAGE_MAPPING = {
    "dry-leaves": "dry-leaves.png",
    "flowers": "flowers.png",
    "energy-products": "energy-products.png",
    "dry-items": "dry-items.png",
    "baby-foods": "baby-foods-category.png",
    "natural-food-colours": "natural-food-colours.png",
    "spices": "spices-category.png",
    "millets-grains": "millets-grains.png",
}

# Responsive hero image variants: (filename-suffix, metadata-key)
# Each collection can have up to 4 responsive variants alongside the default.
RESPONSIVE_HERO_VARIANTS = (
    ("desktop", "hero_desktop"),
    ("desktop-2x", "hero_desktop_retina"),
    ("mobile", "hero_mobile"),
    ("mobile-2x", "hero_mobile_retina"),
)


PRODUCT_NAMES_BY_CATEGORY = {
    "Dry Leaves": [
        "Bay Leaf",
        "Curry Leaf",
        "Drumstick Leaf",
        "Cinnamon Leaf",
        "Sage Leaves",
        "Mint Leaf",
        "Black Pepper Leaf",
        "Tulsi Leaf",
        "Oregano Leaf",
        "Hibiscus Leaf",
    ],
    "Flowers": [
        "Hibiscus Flower",
    ],
    "Energy Products": [
        "Drumstick",
        "Ashwagandha",
        "Watermelon Seeds",
        "Pumpkin Seeds",
        "Sunflower Seed Powder",
        "Pomegranate Dry Powder",
        "Guava Dry Powder",
        "Mucuna Pruriens Powder",
        "Velvet Bean",
        "Chia Seeds",
        "Flax Seeds",
    ],
    "Dry Items": [
        "Garlic",
        "Turmeric",
        "Ginger",
        "Jackfruit",
    ],
    "Baby Foods": [
        "Banana Powder",
        "Arrowroot Powder",
        "Banana Health Mix Powder",
        "Jackfruit Powder",
    ],
    "Natural Food Colours": [
        "Beetroot Powder",
        "Marigold",
        "Mission Grass",
        "Drum Leaf",
    ],
    "Spices": [
        "Clove Buds",
        "Nutmeg Mace",
        "Cinnamon",
        "Black Pepper",
        "Star Anise",
        "Cardamom",
        "Golden Berry",
        "Paprika",
    ],
    "Millets & Grains": [
        "Kodo Millet",
        "Foxtail Millet",
        "Bajra",
    ],
}


DESCRIPTION_TEMPLATES = {
    "Dry Leaves": (
        "{name} is part of Exlynatural's dry leaves collection, selected for "
        "everyday kitchen and pantry use. It is packed as a simple natural "
        "ingredient for home recipes."
    ),
    "Flowers": (
        "{name} is a dried floral ingredient from Exlynatural's natural foods "
        "range. It is suited for customers looking for clean, pantry-friendly "
        "botanical ingredients."
    ),
    "Energy Products": (
        "{name} is part of Exlynatural's seed, fruit, and plant powder range. "
        "It is packed for convenient use in everyday food preparations."
    ),
    "Dry Items": (
        "{name} is a dry pantry ingredient from Exlynatural's natural foods "
        "collection. It is selected for simple home cooking and recipe use."
    ),
    "Baby Foods": (
        "{name} is part of Exlynatural's baby foods collection. Use it as a "
        "basic food ingredient according to your preferred recipe and care "
        "guidance."
    ),
    "Natural Food Colours": (
        "{name} is part of Exlynatural's natural food colour range. It is "
        "intended for adding plant-based colour to suitable food preparations."
    ),
    "Spices": (
        "{name} is a spice from Exlynatural's natural pantry collection. It is "
        "packed for everyday seasoning, cooking, and recipe preparation."
    ),
    "Millets & Grains": (
        "{name} is part of Exlynatural's millets and grains collection. It is "
        "selected as a simple pantry staple for everyday food preparation."
    ),
}


@dataclass(frozen=True)
class ProductSeed:
    category_name: str
    name: str
    slug: str
    sku: str
    description: str
    image_filename: str


@dataclass(frozen=True)
class CollectionSeed:
    name: str
    slug: str
    description: str
    hero_title: str
    hero_subtitle: str
    image_filename: str
    product_slugs: tuple[str, ...]


@dataclass
class SeedStats:
    created: dict[str, int] = field(default_factory=dict)
    reused: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)

    def mark(self, bucket: str, name: str) -> None:
        target = getattr(self, bucket)
        target[name] = target.get(name, 0) + 1


def build_catalog() -> tuple[ProductSeed, ...]:
    products = []
    for category_name, product_names in PRODUCT_NAMES_BY_CATEGORY.items():
        template = DESCRIPTION_TEMPLATES[category_name]
        for product_name in product_names:
            slug = slugify(product_name)
            products.append(
                ProductSeed(
                    category_name=category_name,
                    name=product_name,
                    slug=slug,
                    sku=f"EXN-{slug.upper()}",
                    description=template.format(name=product_name),
                    image_filename=f"{slug}.png",
                )
            )
    return tuple(products)


CATALOG = build_catalog()
PRODUCT_SLUGS_BY_NAME = {product.name: product.slug for product in CATALOG}


def product_slugs(*product_names: str) -> tuple[str, ...]:
    return tuple(PRODUCT_SLUGS_BY_NAME[name] for name in product_names)


def build_collection_seeds() -> tuple[CollectionSeed, ...]:
    all_product_slugs = tuple(product.slug for product in CATALOG)
    return (
        CollectionSeed(
            name="Featured Products",
            slug="featured-products",
            description=(
                "A featured selection of Exlynatural pantry ingredients, "
                "natural food products, spices, seeds, and grains."
            ),
            hero_title="Pure Wellness, Crafted by Nature",
            hero_subtitle=(
                "Featured pantry essentials, botanicals, spices, seeds, "
                "and grains for everyday care."
            ),
            image_filename="featured-products.png",
            product_slugs=all_product_slugs,
        ),
        CollectionSeed(
            name="Botanicals",
            slug="botanicals",
            description=(
                "Dried leaves, flowers, and plant-based colour ingredients "
                "selected for simple pantry and recipe use."
            ),
            hero_title="Botanical Ingredients for Everyday Rituals",
            hero_subtitle=(
                "Dried leaves, flowers, and plant-based colours selected "
                "for natural recipes."
            ),
            image_filename="botanicals.png",
            product_slugs=product_slugs(
                "Bay Leaf",
                "Curry Leaf",
                "Drumstick Leaf",
                "Cinnamon Leaf",
                "Sage Leaves",
                "Mint Leaf",
                "Black Pepper Leaf",
                "Tulsi Leaf",
                "Oregano Leaf",
                "Hibiscus Leaf",
                "Hibiscus Flower",
                "Marigold",
                "Mission Grass",
                "Drum Leaf",
            ),
        ),
        CollectionSeed(
            name="Seeds & Powders",
            slug="seeds-powders",
            description=(
                "Seeds, fruit powders, and plant powders packed for everyday "
                "food preparation."
            ),
            hero_title="Seeds & Powders with Natural Goodness",
            hero_subtitle=(
                "Fruit powders, seed blends, and plant powders packed for "
                "simple daily food preparation."
            ),
            image_filename="seeds-powders.png",
            product_slugs=product_slugs(
                "Ashwagandha",
                "Watermelon Seeds",
                "Pumpkin Seeds",
                "Sunflower Seed Powder",
                "Pomegranate Dry Powder",
                "Guava Dry Powder",
                "Mucuna Pruriens Powder",
                "Chia Seeds",
                "Flax Seeds",
                "Velvet Bean",
                "Beetroot Powder",
            ),
        ),
        CollectionSeed(
            name="Kitchen Pantry",
            slug="kitchen-pantry",
            description=(
                "Spices, dry ingredients, millets, and grains selected for "
                "everyday cooking and pantry use."
            ),
            hero_title="Kitchen Pantry Staples, Naturally Selected",
            hero_subtitle=(
                "Spices, dry ingredients, millets, and grains made for "
                "everyday cooking."
            ),
            image_filename="kitchen-pantry.png",
            product_slugs=product_slugs(
                "Garlic",
                "Turmeric",
                "Ginger",
                "Jackfruit",
                "Drumstick",
                "Clove Buds",
                "Nutmeg Mace",
                "Cinnamon",
                "Black Pepper",
                "Star Anise",
                "Cardamom",
                "Golden Berry",
                "Paprika",
                "Kodo Millet",
                "Foxtail Millet",
                "Bajra",
            ),
        ),
        CollectionSeed(
            name="Baby Foods",
            slug="baby-foods",
            description=(
                "Basic food ingredients for baby food preparations, intended "
                "for use according to preferred recipes and care guidance."
            ),
            hero_title="Gentle Ingredients for Baby Food Recipes",
            hero_subtitle=(
                "Simple powders and mixes for carefully prepared baby food "
                "recipes at home."
            ),
            image_filename="baby-foods.png",
            product_slugs=product_slugs(
                "Banana Powder",
                "Arrowroot Powder",
                "Banana Health Mix Powder",
                "Jackfruit Powder",
            ),
        ),
    )


COLLECTIONS = build_collection_seeds()


class Command(BaseCommand):
    help = "Populate the database with Exlynatural store setup data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--createsuperuser",
            action="store_true",
            dest="createsuperuser",
            default=False,
            help="Create or update the admin account.",
        )
        parser.add_argument("--admin-email", default=DEFAULT_ADMIN_EMAIL)
        parser.add_argument("--admin-password", default=DEFAULT_ADMIN_PASSWORD)
        parser.add_argument("--channel", default=DEFAULT_CHANNEL_SLUG)
        parser.add_argument("--currency", default=DEFAULT_CURRENCY)
        parser.add_argument("--country", default=DEFAULT_COUNTRY)
        parser.add_argument("--price", type=Decimal, default=DEFAULT_PRICE)
        parser.add_argument(
            "--shipping-price",
            type=Decimal,
            default=DEFAULT_SHIPPING_PRICE,
        )
        parser.add_argument("--stock", type=int, default=DEFAULT_STOCK)
        parser.add_argument(
            "--skipsequencereset",
            action="store_true",
            dest="skipsequencereset",
            default=False,
            help="Do not reset SQL sequences after seeding.",
        )

    def handle(self, *args, **options):
        self.stats = SeedStats()

        with transaction.atomic():
            if options["createsuperuser"]:
                self.create_superuser(
                    email=options["admin_email"],
                    password=options["admin_password"],
                )

            channel = self.get_or_create_channel(
                slug=options["channel"],
                currency=options["currency"],
                country=options["country"],
            )
            product_type = self.get_or_create_product_type()
            warehouse = self.get_or_create_warehouse(country=options["country"])
            shipping_zone = self.get_or_create_shipping_zone(
                country=options["country"],
                channel=channel,
                warehouse=warehouse,
            )
            self.get_or_create_shipping_method(
                shipping_zone=shipping_zone,
                channel=channel,
                currency=options["currency"],
                price=options["shipping_price"],
            )
            self.seed_catalog(
                channel=channel,
                product_type=product_type,
                warehouse=warehouse,
                currency=options["currency"],
                price=options["price"],
                stock_quantity=options["stock"],
            )
            self.seed_collections(channel=channel)
            self.seed_pages()
            self.seed_navigation()

        if not options["skipsequencereset"]:
            self.sequence_reset()

        self.print_summary()

    def create_superuser(self, email, password):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        if created:
            self.stats.mark("created", "superusers")
        else:
            self.stats.mark("reused", "superusers")

        changed = False
        for field_name in ["is_staff", "is_superuser", "is_active"]:
            if not getattr(user, field_name):
                setattr(user, field_name, True)
                changed = True
        if created or password:
            user.set_password(password)
            changed = True
        if changed:
            user.save()
            if not created:
                self.stats.mark("updated", "superusers")

    def get_or_create_channel(self, slug, currency, country):
        channel, created = Channel.objects.get_or_create(
            slug=slug,
            defaults={
                "name": "Exlynatural India",
                "is_active": True,
                "currency_code": currency,
                "default_country": country,
            },
        )
        if created:
            self.stats.mark("created", "channels")
            return channel

        changed = False
        updates = {
            "name": channel.name or "Exlynatural India",
            "is_active": True,
            "currency_code": currency,
            "default_country": country,
        }
        for field_name, value in updates.items():
            if getattr(channel, field_name) != value:
                setattr(channel, field_name, value)
                changed = True
        if changed:
            channel.save()
            self.stats.mark("updated", "channels")
        else:
            self.stats.mark("reused", "channels")
        return channel

    def get_or_create_product_type(self):
        product_type, created = ProductType.objects.get_or_create(
            slug="exlynatural-product",
            defaults={
                "name": "Exlynatural Product",
                "kind": ProductTypeKind.NORMAL,
                "is_shipping_required": True,
                "is_digital": False,
                "has_variants": False,
            },
        )
        if created:
            self.stats.mark("created", "product_types")
            return product_type

        changed = False
        updates = {
            "name": "Exlynatural Product",
            "kind": ProductTypeKind.NORMAL,
            "is_shipping_required": True,
            "is_digital": False,
            "has_variants": False,
        }
        for field_name, value in updates.items():
            if getattr(product_type, field_name) != value:
                setattr(product_type, field_name, value)
                changed = True
        if changed:
            product_type.save()
            self.stats.mark("updated", "product_types")
        else:
            self.stats.mark("reused", "product_types")
        return product_type

    def get_or_create_warehouse(self, country):
        address, _ = Address.objects.get_or_create(
            company_name="Exlynatural",
            street_address_1="Exlynatural Main Warehouse",
            city="Kochi",
            country=country,
            defaults={
                "first_name": "Exlynatural",
                "last_name": "Warehouse",
                "postal_code": "682001",
                "country_area": "Kerala",
                "validation_skipped": True,
            },
        )
        warehouse, created = Warehouse.objects.get_or_create(
            slug="exlynatural-main-warehouse",
            defaults={
                "name": "Exlynatural Main Warehouse",
                "address": address,
                "email": "warehouse@exlynatural.local",
                "is_private": False,
            },
        )
        if created:
            self.stats.mark("created", "warehouses")
            return warehouse

        changed = False
        updates = {
            "name": "Exlynatural Main Warehouse",
            "address": address,
            "email": "warehouse@exlynatural.local",
            "is_private": False,
        }
        for field_name, value in updates.items():
            if getattr(warehouse, field_name) != value:
                setattr(warehouse, field_name, value)
                changed = True
        if changed:
            warehouse.save()
            self.stats.mark("updated", "warehouses")
        else:
            self.stats.mark("reused", "warehouses")
        return warehouse

    def get_or_create_shipping_zone(self, country, channel, warehouse):
        shipping_zone, created = ShippingZone.objects.get_or_create(
            name="India Shipping",
            defaults={
                "countries": [country],
                "default": True,
                "description": "Flat-rate shipping across India.",
            },
        )
        if created:
            self.stats.mark("created", "shipping_zones")
        else:
            self.stats.mark("reused", "shipping_zones")

        changed = False
        current_countries = [
            item.code if hasattr(item, "code") else str(item)
            for item in shipping_zone.countries
        ]
        if current_countries != [country]:
            shipping_zone.countries = [country]
            changed = True
        if not shipping_zone.default:
            shipping_zone.default = True
            changed = True
        if changed:
            shipping_zone.save()
            self.stats.mark("updated", "shipping_zones")

        if not shipping_zone.channels.filter(pk=channel.pk).exists():
            shipping_zone.channels.add(channel)
            self.stats.mark("updated", "shipping_zone_channels")
        if not shipping_zone.warehouses.filter(pk=warehouse.pk).exists():
            shipping_zone.warehouses.add(warehouse)
            self.stats.mark("updated", "shipping_zone_warehouses")
        if not warehouse.channels.filter(pk=channel.pk).exists():
            warehouse.channels.add(channel)
            self.stats.mark("updated", "warehouse_channels")

        return shipping_zone

    def get_or_create_shipping_method(self, shipping_zone, channel, currency, price):
        method, created = ShippingMethod.objects.get_or_create(
            shipping_zone=shipping_zone,
            name="India Flat Rate",
            defaults={
                "type": ShippingMethodType.PRICE_BASED,
                "minimum_delivery_days": 3,
                "maximum_delivery_days": 7,
            },
        )
        if created:
            self.stats.mark("created", "shipping_methods")
        else:
            self.stats.mark("reused", "shipping_methods")

        listing, listing_created = ShippingMethodChannelListing.objects.update_or_create(
            shipping_method=method,
            channel=channel,
            defaults={
                "currency": currency,
                "price_amount": price,
                "minimum_order_price_amount": Decimal("0.00"),
                "maximum_order_price_amount": None,
            },
        )
        if listing_created:
            self.stats.mark("created", "shipping_method_listings")
        else:
            self.stats.mark("updated", "shipping_method_listings")
        return listing

    def seed_catalog(
        self,
        channel,
        product_type,
        warehouse,
        currency,
        price,
        stock_quantity,
    ):
        categories = sorted({product.category_name for product in CATALOG})
        category_by_name = {}
        for category_name in categories:
            category = self.get_or_create_category(category_name)
            category_by_name[category_name] = category

        for product_seed in CATALOG:
            self.get_or_create_product(
                product_seed=product_seed,
                category=category_by_name[product_seed.category_name],
                product_type=product_type,
                channel=channel,
                warehouse=warehouse,
                currency=currency,
                price=price,
                stock_quantity=stock_quantity,
            )

    def get_or_create_category(self, name):
        slug = slugify(name)
        category, created = Category.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "description": self.editorjs_description(
                    f"Natural {name.lower()} from Exlynatural."
                ),
                "description_plaintext": f"Natural {name.lower()} from Exlynatural.",
            },
        )
        if created:
            self.stats.mark("created", "categories")
        else:
            if category.name != name:
                category.name = name
                category.save(update_fields=["name"])
                self.stats.mark("updated", "categories")
            else:
                self.stats.mark("reused", "categories")

        self.refresh_category_image(category)
        return category

    def get_or_create_product(
        self,
        product_seed,
        category,
        product_type,
        channel,
        warehouse,
        currency,
        price,
        stock_quantity,
    ):
        product, created = Product.objects.get_or_create(
            slug=product_seed.slug,
            defaults={
                "name": product_seed.name,
                "product_type": product_type,
                "category": category,
                "description": self.editorjs_description(product_seed.description),
                "description_plaintext": product_seed.description,
                "search_document": (
                    f"{product_seed.name} {category.name} Exlynatural"
                ),
                "search_index_dirty": True,
            },
        )
        if created:
            self.stats.mark("created", "products")
        else:
            self.stats.mark("reused", "products")
            updates = {
                "name": product_seed.name,
                "product_type": product_type,
                "category": category,
                "description": self.editorjs_description(product_seed.description),
                "description_plaintext": product_seed.description,
                "search_document": (
                    f"{product_seed.name} {category.name} Exlynatural"
                ),
                "search_index_dirty": True,
            }
            changed_fields = []
            for field_name, value in updates.items():
                if getattr(product, field_name) != value:
                    setattr(product, field_name, value)
                    changed_fields.append(field_name)
            if changed_fields:
                product.save(update_fields=changed_fields)
                self.stats.mark("updated", "products")

        self.refresh_product_media(product=product, product_seed=product_seed)

        ProductChannelListing.objects.update_or_create(
            product=product,
            channel=channel,
            defaults={
                "is_published": True,
                "published_at": timezone.now(),
                "visible_in_listings": True,
                "available_for_purchase_at": timezone.now(),
                "currency": currency,
            },
        )

        variant, variant_created = ProductVariant.objects.get_or_create(
            sku=product_seed.sku,
            defaults={
                "name": product_seed.name,
                "product": product,
                "track_inventory": True,
            },
        )
        if variant_created:
            self.stats.mark("created", "variants")
        else:
            self.stats.mark("reused", "variants")
            changed_fields = []
            if variant.product_id != product.pk:
                variant.product = product
                changed_fields.append("product")
            if variant.name != product_seed.name:
                variant.name = product_seed.name
                changed_fields.append("name")
            if not variant.track_inventory:
                variant.track_inventory = True
                changed_fields.append("track_inventory")
            if changed_fields:
                variant.save(update_fields=changed_fields)
                self.stats.mark("updated", "variants")

        if product.default_variant_id != variant.pk:
            product.default_variant = variant
            product.save(update_fields=["default_variant"])
            self.stats.mark("updated", "default_variants")

        ProductVariantChannelListing.objects.update_or_create(
            variant=variant,
            channel=channel,
            defaults={
                "currency": currency,
                "price_amount": price,
                "discounted_price_amount": price,
            },
        )

        stock, stock_created = Stock.objects.update_or_create(
            warehouse=warehouse,
            product_variant=variant,
            defaults={"quantity": stock_quantity},
        )
        if stock_created:
            self.stats.mark("created", "stocks")
        else:
            self.stats.mark("updated", "stocks")
        return product

    def seed_collections(self, channel):
        products_by_slug = {
            product.slug: product
            for product in Product.objects.filter(
                slug__in=[product.slug for product in CATALOG]
            )
        }

        for collection_seed in COLLECTIONS:
            self.get_or_create_collection(
                collection_seed=collection_seed,
                channel=channel,
                products_by_slug=products_by_slug,
            )

        target_slugs = [collection.slug for collection in COLLECTIONS]
        stale_collections = Collection.objects.filter(
            metadata__seeded_by=SEEDER_NAME,
        ).exclude(slug__in=target_slugs)
        stale_count = stale_collections.count()
        if stale_count:
            for collection in stale_collections:
                if collection.background_image:
                    collection.background_image.delete(save=False)
                collection.delete()
            self.stats.updated["collections_removed"] = (
                self.stats.updated.get("collections_removed", 0) + stale_count
            )

    def get_or_create_collection(self, collection_seed, channel, products_by_slug):
        collection, created = Collection.objects.get_or_create(
            slug=collection_seed.slug,
            defaults={
                "name": collection_seed.name,
                "description": self.editorjs_description(collection_seed.description),
                "metadata": {
                    "seeded_by": SEEDER_NAME,
                    "source_filename": collection_seed.image_filename,
                    "hero_title": collection_seed.hero_title,
                    "hero_subtitle": collection_seed.hero_subtitle,
                },
            },
        )
        if created:
            self.stats.mark("created", "collections")
        else:
            self.stats.mark("reused", "collections")
            updates = {
                "name": collection_seed.name,
                "description": self.editorjs_description(collection_seed.description),
            }
            changed_fields = []
            for field_name, value in updates.items():
                if getattr(collection, field_name) != value:
                    setattr(collection, field_name, value)
                    changed_fields.append(field_name)
            if changed_fields:
                collection.save(update_fields=changed_fields)
                self.stats.mark("updated", "collections")

        collection_products = []
        for product_slug in collection_seed.product_slugs:
            product = products_by_slug.get(product_slug)
            if product is None:
                self.stderr.write(
                    self.style.WARNING(
                        "Missing product for collection "
                        f"{collection_seed.name}: {product_slug}"
                    )
                )
                self.stats.mark("reused", "missing_collection_products")
                continue
            collection_products.append(product)

        current_product_ids = set(
            collection.products.values_list("pk", flat=True)
        )
        target_product_ids = {product.pk for product in collection_products}
        if current_product_ids != target_product_ids:
            collection.products.set(collection_products)
            self.stats.mark("updated", "collection_products")
        else:
            self.stats.mark("reused", "collection_products")

        listing, listing_created = CollectionChannelListing.objects.update_or_create(
            collection=collection,
            channel=channel,
            defaults={
                "is_published": True,
                "published_at": timezone.now(),
            },
        )
        if listing_created:
            self.stats.mark("created", "collection_channel_listings")
        else:
            self.stats.mark("updated", "collection_channel_listings")

        self.refresh_collection_image(
            collection=collection,
            collection_seed=collection_seed,
        )
        return collection

    def refresh_product_media(self, product, product_seed):
        seeded_media = ProductMedia.objects.filter(
            product=product,
            metadata__seeded_by=SEEDER_NAME,
        )
        for media in seeded_media:
            if media.image:
                media.image.delete(save=False)
            media.delete()
            self.stats.mark("updated", "product_media")

        image_path = PRODUCT_IMAGE_DIR / product_seed.image_filename
        if not image_path.exists():
            self.stderr.write(
                self.style.WARNING(
                    f"Missing image for {product_seed.name}: {image_path}"
                )
            )
            self.stats.mark("reused", "missing_product_images")
            return

        with image_path.open("rb") as image_file:
            media = ProductMedia(
                product=product,
                alt=f"{product_seed.name} by Exlynatural",
                sort_order=0,
                metadata={
                    "seeded_by": SEEDER_NAME,
                    "source_filename": product_seed.image_filename,
                },
            )
            media.image.save(
                product_seed.image_filename,
                File(image_file),
                save=False,
            )
            media.save()
        self.stats.mark("created", "product_media")

    def refresh_collection_image(self, collection, collection_seed):
        image_path = COLLECTION_IMAGE_DIR / collection_seed.image_filename
        if not image_path.exists():
            self.stderr.write(
                self.style.WARNING(
                    f"Missing image for {collection_seed.name}: {image_path}"
                )
            )
            self.stats.mark("reused", "missing_collection_images")
            return

        metadata = dict(collection.metadata or {})
        image_is_seeded = metadata.get("seeded_by") == SEEDER_NAME
        if collection.background_image and not image_is_seeded:
            self.stats.mark("reused", "manual_collection_images")
            return

        if collection.background_image:
            collection.background_image.delete(save=False)
            self.stats.mark("updated", "collection_images")

        with image_path.open("rb") as image_file:
            collection.background_image.save(
                collection_seed.image_filename,
                File(image_file),
                save=False,
            )

        collection.background_image_alt = f"{collection_seed.name} by Exlynatural"
        metadata.update(
            {
                "seeded_by": SEEDER_NAME,
                "source_filename": collection_seed.image_filename,
                "hero_title": collection_seed.hero_title,
                "hero_subtitle": collection_seed.hero_subtitle,
            }
        )

        # Upload responsive hero variants and store served URLs in metadata.
        self._upload_responsive_hero_variants(
            collection_seed=collection_seed,
            metadata=metadata,
        )

        collection.metadata = metadata
        collection.save(
            update_fields=[
                "background_image",
                "background_image_alt",
                "metadata",
            ]
        )
        self.stats.mark("created", "collection_images")

    def refresh_category_image(self, category):
        image_filename = CATEGORY_IMAGE_MAPPING.get(category.slug)
        if not image_filename:
            return

        image_path = CATEGORY_IMAGE_DIR / image_filename
        if not image_path.exists():
            self.stderr.write(
                self.style.WARNING(
                    f"Missing image for category {category.name}: {image_path}"
                )
            )
            self.stats.mark("reused", "missing_category_images")
            return

        metadata = dict(category.metadata or {})
        image_is_seeded = metadata.get("seeded_by") == SEEDER_NAME
        if category.background_image and not image_is_seeded:
            self.stats.mark("reused", "manual_category_images")
            return

        if category.background_image:
            category.background_image.delete(save=False)
            self.stats.mark("updated", "category_images")

        with image_path.open("rb") as image_file:
            category.background_image.save(
                image_filename,
                File(image_file),
                save=False,
            )

        category.background_image_alt = f"Natural {category.name.lower()} from Exlynatural"
        metadata.update(
            {
                "seeded_by": SEEDER_NAME,
                "source_filename": image_filename,
            }
        )

        # Upload responsive hero variants and store served URLs in metadata.
        self._upload_responsive_category_variants(
            category=category,
            image_filename=image_filename,
            metadata=metadata,
        )

        category.metadata = metadata
        category.save(
            update_fields=[
                "background_image",
                "background_image_alt",
                "metadata",
            ]
        )
        self.stats.mark("created", "category_images")

    def _upload_responsive_category_variants(self, category, image_filename, metadata):
        """Upload responsive category image variants and store their URLs in metadata."""
        from django.core.files.storage import default_storage

        stem = image_filename.rsplit(".", 1)[0]
        ext = image_filename.rsplit(".", 1)[1]

        # Delete previously seeded responsive variants from storage.
        for _suffix, meta_key in RESPONSIVE_HERO_VARIANTS:
            old_path = metadata.get(meta_key)
            if old_path and default_storage.exists(old_path):
                default_storage.delete(old_path)
                self.stats.mark("updated", "responsive_category_images")

        for suffix, meta_key in RESPONSIVE_HERO_VARIANTS:
            variant_filename = f"{stem}-{suffix}.{ext}"
            variant_path = CATEGORY_IMAGE_DIR / variant_filename
            if not variant_path.exists():
                self.stderr.write(
                    self.style.WARNING(
                        f"Missing responsive variant for category "
                        f"{category.name}: {variant_filename}"
                    )
                )
                self.stats.mark("reused", "missing_responsive_category_images")
                metadata.pop(meta_key, None)
                continue

            storage_name = f"category-heroes/{variant_filename}"
            with variant_path.open("rb") as f:
                saved_name = default_storage.save(storage_name, File(f))
            metadata[meta_key] = saved_name
            self.stats.mark("created", "responsive_category_images")

    def _upload_responsive_hero_variants(self, collection_seed, metadata):
        """Upload responsive hero image variants and store their URLs in metadata."""
        from django.core.files.storage import default_storage

        stem = collection_seed.image_filename.rsplit(".", 1)[0]  # e.g. "featured-products"
        ext = collection_seed.image_filename.rsplit(".", 1)[1]   # e.g. "png"

        # Delete previously seeded responsive variants from storage.
        for _suffix, meta_key in RESPONSIVE_HERO_VARIANTS:
            old_path = metadata.get(meta_key)
            if old_path and default_storage.exists(old_path):
                default_storage.delete(old_path)
                self.stats.mark("updated", "responsive_hero_images")

        for suffix, meta_key in RESPONSIVE_HERO_VARIANTS:
            variant_filename = f"{stem}-{suffix}.{ext}"
            variant_path = COLLECTION_IMAGE_DIR / variant_filename
            if not variant_path.exists():
                self.stderr.write(
                    self.style.WARNING(
                        f"Missing responsive variant for "
                        f"{collection_seed.name}: {variant_filename}"
                    )
                )
                self.stats.mark("reused", "missing_responsive_hero_images")
                # Remove stale metadata key if present.
                metadata.pop(meta_key, None)
                continue

            storage_name = f"collection-heroes/{variant_filename}"
            with variant_path.open("rb") as f:
                saved_name = default_storage.save(storage_name, File(f))
            metadata[meta_key] = saved_name
            self.stats.mark("created", "responsive_hero_images")

    def seed_pages(self):
        """Seed pages, including the homepage hero and middle promo banners."""
        from saleor.page.models import Page, PageType
        from django.core.files.storage import default_storage
        from django.core.files import File

        page_type, created = PageType.objects.get_or_create(
            slug="banner",
            defaults={
                "name": "Banner",
            },
        )
        if created:
            self.stats.mark("created", "page_types")

        PAGE_SEEDS = [
            {
                "slug": "home-banner",
                "title": "Pure Wellness, Crafted by Nature",
                "content": "Discover premium, organic, and ethically-sourced kitchen essentials, spices, and botanicals for your everyday wellness.",
                "filename_prefix": "home-banner",
            },
            {
                "slug": "middle-promo-banner",
                "title": "Harvested at Peak Freshness",
                "content": "We source our spices, botanicals, and grains directly from small organic farms that respect the earth, preserving maximum nutrients and pure flavor.",
                "filename_prefix": "middle-promo",
            }
        ]

        for seed in PAGE_SEEDS:
            page, page_created = Page.objects.get_or_create(
                slug=seed["slug"],
                defaults={
                    "title": seed["title"],
                    "page_type": page_type,
                    "content": self.editorjs_description(seed["content"]),
                    "is_published": True,
                    "published_at": timezone.now(),
                },
            )

            metadata = dict(page.metadata or {})
            metadata.update({"seeded_by": SEEDER_NAME})

            # Upload base banner image
            base_filename = f"{seed['filename_prefix']}.png"
            base_banner_path = APP_DIR / "seed-assets" / "exlynatural" / "banners" / base_filename
            if base_banner_path.exists():
                storage_name = f"page-banners/{base_filename}"
                if default_storage.exists(storage_name):
                    default_storage.delete(storage_name)
                with base_banner_path.open("rb") as f:
                    saved_name = default_storage.save(storage_name, File(f))
                metadata["background_image"] = saved_name

            variants = (
                ("desktop", "hero_desktop"),
                ("desktop-2x", "hero_desktop_retina"),
                ("mobile", "hero_mobile"),
                ("mobile-2x", "hero_mobile_retina"),
            )
            for suffix, meta_key in variants:
                variant_filename = f"{seed['filename_prefix']}-{suffix}.png"
                variant_path = APP_DIR / "seed-assets" / "exlynatural" / "banners" / variant_filename
                if variant_path.exists():
                    storage_name = f"page-banners/{variant_filename}"
                    if default_storage.exists(storage_name):
                        default_storage.delete(storage_name)
                    with variant_path.open("rb") as f:
                        saved_name = default_storage.save(storage_name, File(f))
                    metadata[meta_key] = saved_name
                    self.stats.mark("created", "responsive_hero_images")

            page.metadata = metadata
            page.save(update_fields=["metadata"])

            if page_created:
                self.stats.mark("created", "pages")
            else:
                self.stats.mark("updated", "pages")

    def seed_navigation(self):
        menu, created = Menu.objects.get_or_create(
            slug=NAVBAR_MENU_SLUG,
            defaults={
                "name": "Navbar",
                "metadata": {"seeded_by": SEEDER_NAME},
            },
        )
        if created:
            self.stats.mark("created", "menus")
        else:
            self.stats.mark("reused", "menus")
            changed_fields = []
            if menu.name != "Navbar":
                menu.name = "Navbar"
                changed_fields.append("name")
            metadata = dict(menu.metadata or {})
            if metadata.get("seeded_by") != SEEDER_NAME:
                metadata["seeded_by"] = SEEDER_NAME
                menu.metadata = metadata
                changed_fields.append("metadata")
            if changed_fields:
                menu.save(update_fields=changed_fields)
                self.stats.mark("updated", "menus")

        collection_by_slug = {
            collection.slug: collection
            for collection in Collection.objects.filter(
                slug__in=[collection.slug for collection in COLLECTIONS]
            )
        }
        target_seed_keys = set()
        for sort_order, collection_seed in enumerate(COLLECTIONS):
            collection = collection_by_slug.get(collection_seed.slug)
            if collection is None:
                self.stderr.write(
                    self.style.WARNING(
                        "Missing collection for navigation: "
                        f"{collection_seed.slug}"
                    )
                )
                self.stats.mark("reused", "missing_navigation_collections")
                continue

            seed_key = f"collection:{collection_seed.slug}"
            target_seed_keys.add(seed_key)
            menu_item = MenuItem.objects.filter(
                menu=menu,
                metadata__seeded_by=SEEDER_NAME,
                metadata__seed_key=seed_key,
            ).first()
            item_created = False
            if menu_item is None:
                menu_item = MenuItem(
                    menu=menu,
                    name=collection_seed.name,
                    collection=collection,
                    sort_order=sort_order,
                    metadata={
                        "seeded_by": SEEDER_NAME,
                        "seed_key": seed_key,
                    },
                )
                item_created = True

            updates = {
                "name": collection_seed.name,
                "collection": collection,
                "category": None,
                "page": None,
                "url": "",
                "parent": None,
                "sort_order": sort_order,
            }
            changed_fields = []
            for field_name, value in updates.items():
                if getattr(menu_item, field_name) != value:
                    setattr(menu_item, field_name, value)
                    changed_fields.append(field_name)
            metadata = dict(menu_item.metadata or {})
            if metadata.get("seeded_by") != SEEDER_NAME:
                metadata["seeded_by"] = SEEDER_NAME
                changed_fields.append("metadata")
            if metadata.get("seed_key") != seed_key:
                metadata["seed_key"] = seed_key
                changed_fields.append("metadata")
            menu_item.metadata = metadata

            if item_created:
                menu_item.save()
                self.stats.mark("created", "menu_items")
            elif changed_fields:
                menu_item.save(update_fields=sorted(set(changed_fields)))
                self.stats.mark("updated", "menu_items")
            else:
                self.stats.mark("reused", "menu_items")

        stale_seeded_items = MenuItem.objects.filter(
            menu=menu,
            metadata__seeded_by=SEEDER_NAME,
        ).exclude(metadata__seed_key__in=target_seed_keys)
        stale_count = stale_seeded_items.count()
        if stale_count:
            stale_seeded_items.delete()
            self.stats.updated["menu_items_removed"] = (
                self.stats.updated.get("menu_items_removed", 0) + stale_count
            )

    def editorjs_description(self, text):
        return {"blocks": [{"type": "paragraph", "data": {"text": text}}]}

    def sequence_reset(self):
        commands = StringIO()
        for app in apps.get_app_configs():
            if "saleor" in app.name:
                call_command(
                    "sqlsequencereset",
                    app.label,
                    stdout=commands,
                    no_color=True,
                )
        sql = commands.getvalue()
        if sql:
            with connection.cursor() as cursor:
                cursor.execute(sql)

    def print_summary(self):
        self.stdout.write(self.style.SUCCESS("Exlynatural seed complete."))
        for label, bucket in [
            ("Created", self.stats.created),
            ("Reused", self.stats.reused),
            ("Updated", self.stats.updated),
        ]:
            if not bucket:
                continue
            self.stdout.write(label + ":")
            for key in sorted(bucket):
                self.stdout.write(f"  {key}: {bucket[key]}")
