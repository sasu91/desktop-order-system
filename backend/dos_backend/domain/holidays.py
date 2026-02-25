"""
Holiday and Closure Management Module.

Supports:
- Italian public holidays (including Easter calculations)
- Custom closures (store/warehouse/supplier)
- Effects: no_order, no_receipt, or both
- Rule types: single-date, range, fixed-date (annual recurrence)
"""
from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Any
from enum import Enum
import json
from pathlib import Path


class HolidayEffect(Enum):
    """Effect of a holiday/closure on operations."""
    NO_ORDER = "no_order"      # Cannot place orders
    NO_RECEIPT = "no_receipt"  # Cannot receive deliveries
    BOTH = "both"              # No orders AND no receipts


class HolidayType(Enum):
    """Type of holiday rule."""
    SINGLE_DATE = "single"     # One-time date (ISO format)
    RANGE = "range"            # Date range (start-end)
    FIXED_DATE = "fixed"       # Annual recurrence (MM-DD)


@dataclass
class HolidayRule:
    """
    Definition of a holiday or closure.
    
    Attributes:
        name: Human-readable name (e.g., "Natale", "Chiusura estiva")
        scope: Context ("system", "store", "warehouse", "supplier")
        effect: What is blocked (no_order, no_receipt, both)
        type: Rule type (single, range, fixed)
        params: Type-specific parameters:
            - single: {"date": "YYYY-MM-DD"}
            - range: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
            - fixed (annual): {"month": int, "day": int} - e.g., December 25
            - fixed (monthly): {"day": int} - e.g., 1st of every month
    """
    name: str
    scope: str
    effect: HolidayEffect
    type: HolidayType
    params: Dict[str, Any]
    
    def applies_to_date(self, check_date: date, year: Optional[int] = None) -> bool:
        """
        Check if this rule applies to a specific date.
        
        Args:
            check_date: Date to check
            year: Year context for fixed-date rules (defaults to check_date.year)
            
        Returns:
            True if rule applies to this date
        """
        if year is None:
            year = check_date.year
            
        if self.type == HolidayType.SINGLE_DATE:
            rule_date = date.fromisoformat(self.params["date"])
            return check_date == rule_date
            
        elif self.type == HolidayType.RANGE:
            start = date.fromisoformat(self.params["start"])
            end = date.fromisoformat(self.params["end"])
            return start <= check_date <= end
            
        elif self.type == HolidayType.FIXED_DATE:
            # Monthly recurrence: only day specified (e.g., "1st of every month")
            if "month" not in self.params:
                return check_date.day == self.params["day"]
            # Annual recurrence: month + day specified (e.g., "December 25")
            else:
                return (check_date.month == self.params["month"] and
                        check_date.day == self.params["day"])
        
        return False


def easter_sunday(year: int) -> date:
    """
    Calculate Easter Sunday using the Meeus/Jones/Butcher algorithm (Gregorian).
    
    Args:
        year: Year to calculate Easter for
        
    Returns:
        Date of Easter Sunday
        
    Reference:
        https://en.wikipedia.org/wiki/Date_of_Easter#Anonymous_Gregorian_algorithm
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    
    return date(year, month, day)


def italian_public_holidays(year: int) -> Dict[date, str]:
    """
    Generate Italian public holidays for a given year.
    
    Includes:
    - Fixed holidays: Capodanno, Epifania, Liberazione, Lavoro, Repubblica,
      Ferragosto, Ognissanti, Immacolata, Natale, S.Stefano
    - Mobile holidays: Pasqua, Lunedì dell'Angelo (Easter Monday)
    
    Note: Patron saints vary by location and must be configured separately.
    
    Args:
        year: Year to generate holidays for
        
    Returns:
        Dictionary mapping date to holiday name
    """
    holidays = {}
    
    # Fixed-date holidays
    holidays[date(year, 1, 1)] = "Capodanno"
    holidays[date(year, 1, 6)] = "Epifania"
    holidays[date(year, 4, 25)] = "Liberazione"
    holidays[date(year, 5, 1)] = "Festa del Lavoro"
    holidays[date(year, 6, 2)] = "Festa della Repubblica"
    holidays[date(year, 8, 15)] = "Ferragosto"
    holidays[date(year, 11, 1)] = "Ognissanti"
    holidays[date(year, 12, 8)] = "Immacolata Concezione"
    holidays[date(year, 12, 25)] = "Natale"
    holidays[date(year, 12, 26)] = "Santo Stefano"
    
    # Mobile holidays (Easter-based)
    easter = easter_sunday(year)
    holidays[easter] = "Pasqua"
    holidays[easter + timedelta(days=1)] = "Lunedì dell'Angelo"
    
    return holidays


@dataclass
class HolidayCalendar:
    """
    Unified holiday and closure calendar.
    
    Manages system holidays (Italian public) + custom closures with precedence.
    """
    rules: List[HolidayRule] = field(default_factory=list)
    _cache: Dict[int, Dict[date, Set[HolidayEffect]]] = field(default_factory=dict, repr=False)
    
    @classmethod
    def from_config(cls, config_path: Path) -> 'HolidayCalendar':
        """
        Load holiday calendar from JSON config file.
        
        Fallback: If file missing or invalid, returns calendar with only Italian public holidays.
        
        Args:
            config_path: Path to holidays.json
            
        Returns:
            HolidayCalendar instance
        """
        rules = []
        
        # Try to load custom config
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                # Validate and parse rules
                for rule_data in config.get("holidays", []):
                    try:
                        effect_str = rule_data.get("effect", "both")
                        effect = HolidayEffect(effect_str)
                        
                        type_str = rule_data.get("type", "single")
                        rule_type = HolidayType(type_str)
                        
                        rule = HolidayRule(
                            name=rule_data["name"],
                            scope=rule_data.get("scope", "system"),
                            effect=effect,
                            type=rule_type,
                            params=rule_data.get("params", {})
                        )
                        
                        # Validate params based on type
                        if rule.type == HolidayType.SINGLE_DATE:
                            if "date" not in rule.params:
                                raise ValueError(f"Single-date rule '{rule.name}' missing 'date' param")
                            date.fromisoformat(rule.params["date"])  # Validate ISO format
                            
                        elif rule.type == HolidayType.RANGE:
                            if "start" not in rule.params or "end" not in rule.params:
                                raise ValueError(f"Range rule '{rule.name}' missing start/end params")
                            start = date.fromisoformat(rule.params["start"])
                            end = date.fromisoformat(rule.params["end"])
                            if start > end:
                                raise ValueError(f"Range rule '{rule.name}': start > end")
                                
                        elif rule.type == HolidayType.FIXED_DATE:
                            # Monthly recurrence: only day required (e.g., "1st of every month")
                            # Annual recurrence: month + day required (e.g., "December 25")
                            if "day" not in rule.params:
                                raise ValueError(f"Fixed-date rule '{rule.name}' missing 'day' param")
                            
                            day = int(rule.params["day"])
                            if not (1 <= day <= 31):
                                raise ValueError(f"Invalid day {day} in rule '{rule.name}'")
                            
                            # If month is specified, validate it (annual recurrence)
                            if "month" in rule.params:
                                month = int(rule.params["month"])
                                if not (1 <= month <= 12):
                                    raise ValueError(f"Invalid month {month} in rule '{rule.name}'")
                        
                        rules.append(rule)
                        
                    except (KeyError, ValueError) as e:
                        print(f"Warning: Skipping invalid holiday rule: {e}")
                        continue
                        
            except (json.JSONDecodeError, FileNotFoundError) as e:
                print(f"Warning: Could not load holidays config from {config_path}: {e}")
                print("Falling back to Italian public holidays only.")
        
        # Add Italian public holidays as system rules if not in config
        # (config can override by using same date with different effect)
        italian_rules = cls._italian_system_rules()
        rules.extend(italian_rules)
        
        return cls(rules=rules)
    
    @staticmethod
    def _italian_system_rules() -> List[HolidayRule]:
        """Generate HolidayRule objects for Italian public holidays."""
        rules = []
        
        # Fixed-date Italian holidays
        fixed_holidays = [
            ("Capodanno", 1, 1),
            ("Epifania", 1, 6),
            ("Liberazione", 4, 25),
            ("Festa del Lavoro", 5, 1),
            ("Festa della Repubblica", 6, 2),
            ("Ferragosto", 8, 15),
            ("Ognissanti", 11, 1),
            ("Immacolata Concezione", 12, 8),
            ("Natale", 12, 25),
            ("Santo Stefano", 12, 26),
        ]
        
        for name, month, day in fixed_holidays:
            rules.append(HolidayRule(
                name=name,
                scope="system",
                effect=HolidayEffect.BOTH,
                type=HolidayType.FIXED_DATE,
                params={"month": month, "day": day}
            ))
        
        # Easter-based holidays are handled dynamically in is_holiday()
        
        return rules
    
    def is_holiday(
        self,
        check_date: date,
        scope: Optional[str] = None,
        effect: Optional[HolidayEffect] = None
    ) -> bool:
        """
        Check if a date is a holiday with optional scope/effect filtering.
        
        Args:
            check_date: Date to check
            scope: Filter by scope (None = any scope)
            effect: Filter by effect (None = any effect)
            
        Returns:
            True if date matches a holiday rule with given filters
        """
        # Check Easter-based holidays first (not in rules list)
        easter = easter_sunday(check_date.year)
        easter_monday = easter + timedelta(days=1)
        
        if check_date == easter or check_date == easter_monday:
            # Easter is always system scope with BOTH effect
            if scope is not None and scope != "system":
                pass  # Skip if filtering for different scope
            elif effect is not None and effect != HolidayEffect.BOTH:
                pass  # Skip if filtering for specific effect
            else:
                return True
        
        # Check configured rules
        for rule in self.rules:
            if not rule.applies_to_date(check_date):
                continue
                
            # Apply filters
            if scope is not None and rule.scope != scope:
                continue
            if effect is not None and rule.effect != effect and rule.effect != HolidayEffect.BOTH:
                continue
                
            return True
        
        return False
    
    def effects_on(self, check_date: date, scope: Optional[str] = None) -> Set[str]:
        """
        Get all effects active on a date for a given scope.
        
        Args:
            check_date: Date to check
            scope: Filter by scope (None = all scopes)
            
        Returns:
            Set of effect strings active on this date
        """
        effects = set()
        
        # Check Easter-based holidays
        easter = easter_sunday(check_date.year)
        easter_monday = easter + timedelta(days=1)
        
        if check_date == easter or check_date == easter_monday:
            if scope is None or scope == "system":
                effects.add("no_order")
                effects.add("no_receipt")
        
        # Check configured rules
        for rule in self.rules:
            if not rule.applies_to_date(check_date):
                continue
            if scope is not None and rule.scope != scope:
                continue
                
            if rule.effect == HolidayEffect.BOTH:
                effects.add("no_order")
                effects.add("no_receipt")
            elif rule.effect == HolidayEffect.NO_ORDER:
                effects.add("no_order")
            elif rule.effect == HolidayEffect.NO_RECEIPT:
                effects.add("no_receipt")
        
        return effects
    
    def list_holidays(self, year: int, scope: Optional[str] = None) -> List[date]:
        """
        List all holiday dates for a given year and optional scope.
        
        Args:
            year: Year to list holidays for
            scope: Filter by scope (None = all scopes)
            
        Returns:
            Sorted list of holiday dates
        """
        holidays = set()
        
        # Add Easter-based holidays
        if scope is None or scope == "system":
            easter = easter_sunday(year)
            holidays.add(easter)
            holidays.add(easter + timedelta(days=1))
        
        # Add configured rules
        for rule in self.rules:
            if scope is not None and rule.scope != scope:
                continue
                
            if rule.type == HolidayType.SINGLE_DATE:
                rule_date = date.fromisoformat(rule.params["date"])
                if rule_date.year == year:
                    holidays.add(rule_date)
                    
            elif rule.type == HolidayType.RANGE:
                start = date.fromisoformat(rule.params["start"])
                end = date.fromisoformat(rule.params["end"])
                current = start
                while current <= end:
                    if current.year == year:
                        holidays.add(current)
                    current += timedelta(days=1)
                    
            elif rule.type == HolidayType.FIXED_DATE:
                try:
                    holiday_date = date(year, rule.params["month"], rule.params["day"])
                    holidays.add(holiday_date)
                except ValueError:
                    # Invalid date (e.g., Feb 30)
                    pass
        
        return sorted(holidays)
