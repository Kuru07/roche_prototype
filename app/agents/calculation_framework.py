from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from app.config import CALCULATION_PENDING_MSG, CALCULATION_PENDING_DETAILS

@dataclass
class CalculationResult:
    status: str  # "NOT_CALCULATED" or "CALCULATED"
    message: str
    details: str
    reference_price: Optional[float] = None
    currency: Optional[str] = None
    audit_trace: list[dict] = field(default_factory=list)
    formula_name: str = "PENDING_OFFICIAL_ROCHE_APPROVAL"
    assumed_formula_applied: bool = False

class BasePricingCalculator(ABC):
    """
    Abstract interface for official Roche reference pricing methodology.
    Third-party or unofficial formulas are strictly prohibited from implementing
    Roche's official methodology without certified validation.
    """
    formula_name: str = "AbstractBaseCalculator"
    
    @abstractmethod
    def calculate(self, basket_records: list[dict], params: dict) -> CalculationResult:
        """Computes official reference price using verified methodology."""
        pass

class FormulaRegistry:
    """
    Registry for reference pricing calculators.
    Defaults strictly to None to prevent assumed formula execution.
    """
    _official_calculator: Optional[BasePricingCalculator] = None

    @classmethod
    def get_calculator(cls) -> Optional[BasePricingCalculator]:
        return cls._official_calculator

    @classmethod
    def register_official_formula(cls, calculator: BasePricingCalculator):
        """Registers the certified official Roche formula once approved."""
        cls._official_calculator = calculator

    @classmethod
    def reset(cls):
        cls._official_calculator = None

def execute_pricing_calculation(query: str, basket_records: list[dict], target_country: str = "Ecuador") -> CalculationResult:
    """
    Entrypoint for pricing calculation queries.
    Enforces the mandatory requirement:
    Unless an official Roche formula is configured, return:
    'NOT CALCULATED — Official Roche methodology pending.'
    Never produce a final reference price using an assumed formula.
    """
    calculator = FormulaRegistry.get_calculator()
    
    if calculator is None:
        return CalculationResult(
            status="NOT_CALCULATED",
            message=CALCULATION_PENDING_MSG,
            details=CALCULATION_PENDING_DETAILS,
            reference_price=None,
            currency=None,
            audit_trace=[
                {
                    "step": "Registry Check",
                    "status": "Awaiting Official Specification",
                    "target_country": target_country,
                    "basket_records_available": len(basket_records),
                    "policy": "Assumed formulas (Lowest, Median, Avg-of-3, unapproved PPP) strictly disabled"
                }
            ],
            formula_name="PENDING_OFFICIAL_ROCHE_APPROVAL",
            assumed_formula_applied=False
        )
    
    # If official formula is configured in the future, execute it deterministically
    return calculator.calculate(basket_records, {"query": query, "target_country": target_country})

