"""FastAPI dependencies for database sessions, authentication, and services."""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.common.exceptions import UnauthorizedException
from app.common.security import decode_access_token

# Type alias for database session dependency
DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    token: dict = Depends(decode_access_token),
) -> "User":  # noqa: F821
    """Extract and validate the current user from the JWT access token."""
    from app.modules.auth.models import User
    
    user = db.query(User).filter(User.id == token["sub"]).first()
    
    if user is None:
        raise UnauthorizedException("User not found")
    
    if not user.is_active:
        raise UnauthorizedException("User account is inactive")
    
    return user


# Type alias for current user dependency
CurrentUser = Annotated["User", Depends(get_current_user)]  # noqa: F821

def get_customer_service(db: DbSession):
    """Provide a CustomerService scoped to the current request session."""
    from app.modules.customers.service import CustomerService
    return CustomerService(db)


CustomerServiceDep = Annotated["CustomerService", Depends(get_customer_service)]  # noqa: F821

def get_invoice_service(db: DbSession):
    """Provide a InvoiceService scoped to the current request session."""
    from app.modules.invoices.service import InvoiceService
    return InvoiceService(db)


InvoiceServiceDep = Annotated["InvoiceService", Depends(get_invoice_service)]  # noqa: F821


def get_quote_service(db: DbSession):
    """Provide a QuoteService scoped to the current request session."""
    from app.modules.quotes.service import QuoteService
    return QuoteService(db)


QuoteServiceDep = Annotated["QuoteService", Depends(get_quote_service)]  # noqa: F821

def get_expense_service(db: DbSession) -> "ExpenseService":
    """Provide an ExpenseService scoped to the current request session."""
    from app.modules.expenses.service import ExpenseService
    return ExpenseService(db)


ExpenseServiceDep = Annotated["ExpenseService", Depends(get_expense_service)]

def get_vendor_service(db: DbSession):
    """Provide a VendorService scoped to the current request session."""
    from app.modules.vendors.service import VendorService
    return VendorService(db)


VendorServiceDep = Annotated["VendorService", Depends(get_vendor_service)]