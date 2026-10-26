from datetime import datetime
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.sale import Sale
from app.models.user import User
from app.schemas.sale import SaleCreate, SaleListResponse, SaleResponse
from app.services.currency_service import convert_currency


class SaleController:

    @staticmethod
    async def create_sale(db: AsyncSession, user_id: int, data: SaleCreate) -> SaleResponse:

        sale = Sale(
            user_id=user_id,
            customer_id=data.customer_id,
            amount=data.amount,
            currency=data.currency.upper(),
            product=data.product,
            date=data.date or datetime.utcnow()
        )

        db.add(sale)

        await db.commit()
        await db.refresh(sale)

        return SaleResponse.model_validate(sale)

    @staticmethod
    async def get_sales(db: AsyncSession, user_id: int) -> SaleListResponse:

        user_result = await db.execute(
            select(User).where(User.id == user_id)
        )

        user = user_result.scalar_one()

        preferred_currency = user.preferred_currency or "USD"

        result = await db.execute(
            select(Sale)
            .where(Sale.user_id == user_id)
            .order_by(Sale.date.desc())
        )

        sales = result.scalars().all()

        converted_sales = []

        for sale in sales:

            converted_amount = convert_currency(
                sale.amount,
                sale.currency,
                preferred_currency
            )

            # Convert the response only; never dirty the stored payment amount.
            converted_sales.append(SaleResponse.model_validate(sale).model_copy(update={
                "amount": converted_amount, "currency": preferred_currency,
            }))

        total = await db.execute(
            select(func.count()).where(Sale.user_id == user_id)
        )

        return SaleListResponse(
            items=converted_sales,
            total=total.scalar()
        )

    @staticmethod
    async def delete_sale(db: AsyncSession, sale_id: int, user_id: int) -> None:

        result = await db.execute(
            select(Sale).where(
                Sale.id == sale_id,
                Sale.user_id == user_id
            )
        )

        sale = result.scalar_one_or_none()

        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sale not found"
            )

        await db.delete(sale)

        await db.commit()
