from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate


class ProductController:

    @staticmethod
    async def list_for_user(db: AsyncSession, user_id: int):
        result = await db.execute(
            select(Product)
            .where(Product.user_id == user_id)
            .order_by(Product.sort_order.asc(), Product.created_at.asc())
        )
        return result.scalars().all()

    @staticmethod
    async def create(db: AsyncSession, user_id: int, data: ProductCreate):
        p = Product(user_id=user_id, **data.model_dump())
        db.add(p)
        await db.commit()
        await db.refresh(p)
        return p

    @staticmethod
    async def update(db: AsyncSession, user_id: int, product_id: int, data: ProductUpdate):
        result = await db.execute(
            select(Product).where(Product.id == product_id, Product.user_id == user_id)
        )
        p = result.scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail="Product not found")
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(p, k, v)
        await db.commit()
        await db.refresh(p)
        return p

    @staticmethod
    async def delete(db: AsyncSession, user_id: int, product_id: int) -> None:
        result = await db.execute(
            select(Product).where(Product.id == product_id, Product.user_id == user_id)
        )
        p = result.scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail="Product not found")
        await db.delete(p)
        await db.commit()

    @staticmethod
    async def toggle_available(db: AsyncSession, user_id: int, product_id: int):
        result = await db.execute(
            select(Product).where(Product.id == product_id, Product.user_id == user_id)
        )
        p = result.scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail="Product not found")
        p.is_available_today = not p.is_available_today
        await db.commit()
        await db.refresh(p)
        return p
