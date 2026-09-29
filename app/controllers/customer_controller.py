from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
from datetime import datetime

from app.models.customer import Customer, CustomerType
from app.schemas.customer import CustomerCreate, CustomerUpdate, CustomerListResponse


class CustomerController:
    
    @staticmethod
    async def create_customer(db: AsyncSession, customer_data: CustomerCreate, user_id: int) -> Customer:
        """Create a new customer for the authenticated user"""
        # Check if customer with same email or phone already exists for this user
        if customer_data.phone_number:
            existing_phone = await CustomerController._get_customer_by_phone(db, customer_data.phone_number, user_id)
            if existing_phone:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Customer with this phone number already exists"
                )
        if customer_data.email:
            existing_email = await CustomerController._get_customer_by_email(db, customer_data.email, user_id)
            if existing_email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Customer with this email already exists"
                )
                # Create new customer
        db_customer = Customer(
            name=customer_data.name,
            phone_number=customer_data.phone_number,
            email=customer_data.email,
            date_of_birth=customer_data.date_of_birth,
            customer_type=customer_data.customer_type,
            user_id=user_id  # Assuming Customer model has user_id foreign key
        )
        
        db.add(db_customer)
        await db.commit()
        await db.refresh(db_customer)
        return db_customer
    
    @staticmethod
    async def get_customers(
        db: AsyncSession, 
        user_id: int,
        page: int = 1, 
        per_page: int = 20,
        search: Optional[str] = None,
        customer_type: Optional[CustomerType] = None
    ) -> CustomerListResponse:
        """Get paginated list of customers for the authenticated user"""
        # Build base query
        base_query = select(Customer).where(Customer.user_id == user_id)
        count_query = select(func.count()).select_from(Customer).where(Customer.user_id == user_id)
        
        # Apply filters
        filters = []
        
        if search:
            search_term = f"%{search}%"
            filters.append(
                or_(
                    Customer.name.ilike(search_term),
                    Customer.email.ilike(search_term),
                    Customer.phone_number.ilike(search_term)
                )
            )
        
        if customer_type:
            filters.append(Customer.customer_type == customer_type)
        
        if filters:
            base_query = base_query.where(and_(*filters))
            count_query = count_query.where(and_(*filters))
        
        # Get total count
        total_result = await db.execute(count_query)
        total = total_result.scalar()
        
        # Apply pagination
        offset = (page - 1) * per_page
        query = base_query.offset(offset).limit(per_page).order_by(Customer.created_at.desc())
        
        result = await db.execute(query)
        customers = result.scalars().all()
        
        total_pages = (total + per_page - 1) // per_page
        
        return CustomerListResponse(
        items=customers,
            total=total,
            page=page,
            per_page=per_page,
            total_pages=total_pages
        )
    
    @staticmethod
    async def get_customer_by_id(db: AsyncSession, customer_id: int, user_id: int) -> Customer:
        """Get a specific customer by ID for the authenticated user"""
        query = select(Customer).where(
            and_(Customer.id == customer_id, Customer.user_id == user_id)
        )
        result = await db.execute(query)
        customer = result.scalar_one_or_none()
        
        if not customer:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Customer not found"
            )
        
        return customer
    
    @staticmethod
    async def update_customer(
        db: AsyncSession, 
        customer_id: int, 
        customer_data: CustomerUpdate, 
        user_id: int
    ) -> Customer:
        """Update a customer for the authenticated user"""
        customer = await CustomerController.get_customer_by_id(db, customer_id, user_id)
        
        # Check for duplicate email or phone if updating those fields
        if customer_data.email and customer_data.email != customer.email:
            existing = await CustomerController._get_customer_by_email(db, customer_data.email, user_id)
            if existing and existing.id != customer_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Customer with this email already exists"
                )
        
        if customer_data.phone_number and customer_data.phone_number != customer.phone_number:
            existing = await CustomerController._get_customer_by_phone(db, customer_data.phone_number, user_id)
            if existing and existing.id != customer_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Customer with this phone number already exists"
                )
        
        # Update fields
        update_data = customer_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(customer, field, value)
        
        customer.updated_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(customer)
        return customer
    
    @staticmethod
    async def delete_customer(db: AsyncSession, customer_id: int, user_id: int) -> bool:
        """Delete a customer and their related sales records for the authenticated user"""
        from sqlalchemy import delete
        from app.models.sale import Sale

        customer = await CustomerController.get_customer_by_id(db, customer_id, user_id)

        await db.execute(delete(Sale).where(Sale.customer_id == customer_id))
        await db.delete(customer)
        await db.commit()
        return True
    
    @staticmethod
    async def _get_customer_by_email_or_phone(
        db: AsyncSession, 
        email: str, 
        phone: str, 
        user_id: int
    ) -> Optional[Customer]:
        """Get customer by email or phone for a specific user"""
        query = select(Customer).where(
            and_(
                Customer.user_id == user_id,
                or_(Customer.email == email, Customer.phone_number == phone)
            )
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()
    
    @staticmethod
    async def _get_customer_by_email(db: AsyncSession, email: str, user_id: int) -> Optional[Customer]:
        """Get customer by email for a specific user"""
        query = select(Customer).where(
            and_(Customer.email == email, Customer.user_id == user_id)
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()
    
    @staticmethod
    async def _get_customer_by_phone(db: AsyncSession, phone: str, user_id: int) -> Optional[Customer]:
        """Get customer by phone for a specific user"""
        query = select(Customer).where(
            and_(Customer.phone_number == phone, Customer.user_id == user_id)
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

