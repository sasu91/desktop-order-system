"""
SKU CSV Import Workflow

Provides safe bulk import of SKU data from external CSV files with:
- Auto-mapping and manual column remapping
- Validation (types, ranges, duplicates)
- Preview with valid/discarded counts
- UPSERT (update + insert) and REPLACE modes
- Backup and atomic write
- Audit logging with detailed error export
"""

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set, Any, Sequence
from datetime import datetime

from src.domain.models import SKU, DemandVariability
from src.domain.ledger import validate_ean


logger = logging.getLogger(__name__)


# Column mappings: canonical field name -> list of accepted aliases (case-insensitive)
COLUMN_ALIASES = {
    "sku": ["sku", "code", "item_code", "product_code", "sku_code"],
    "description": ["description", "desc", "name", "product_name", "item_name"],
    "ean": ["ean", "ean13", "barcode", "gtin", "upc"],
    "moq": ["moq", "min_order_qty", "minimum_order_quantity"],
    "pack_size": ["pack_size", "pack", "pack_qty", "case_size"],
    "lead_time_days": ["lead_time_days", "lead_time", "leadtime", "delivery_days"],
    "review_period": ["review_period", "review_days"],
    "safety_stock": ["safety_stock", "safety", "ss"],
    "shelf_life_days": ["shelf_life_days", "shelf_life", "shelflife", "expiry_days"],
    "min_shelf_life_days": ["min_shelf_life_days", "min_shelf_life", "min_residual_life"],
    "waste_penalty_mode": ["waste_penalty_mode", "penalty_mode"],
    "waste_penalty_factor": ["waste_penalty_factor", "penalty_factor"],
    "waste_risk_threshold": ["waste_risk_threshold", "waste_threshold"],
    "max_stock": ["max_stock", "max", "maximum_stock"],
    "reorder_point": ["reorder_point", "rop", "reorder"],
    "demand_variability": ["demand_variability", "variability", "demand_var"],
    "category": ["category", "cat", "product_category", "sotto_famiglia", "sottofamiglia", "sub_family", "sub-family"],
    "department": ["department", "dept", "famiglia", "family"],
    "oos_boost_percent": ["oos_boost_percent", "oos_boost"],
    "oos_detection_mode": ["oos_detection_mode", "oos_mode"],
    "oos_popup_preference": ["oos_popup_preference", "oos_popup"],
    "forecast_method": ["forecast_method", "forecast"],
    "mc_distribution": ["mc_distribution"],
    "mc_n_simulations": ["mc_n_simulations"],
    "mc_random_seed": ["mc_random_seed"],
    "mc_output_stat": ["mc_output_stat"],
    "mc_output_percentile": ["mc_output_percentile"],
    "mc_horizon_mode": ["mc_horizon_mode"],
    "mc_horizon_days": ["mc_horizon_days"],
    "in_assortment": ["in_assortment", "active", "enabled", "status"],
    "target_csl": ["target_csl", "csl", "service_level"],
}

# Critical fields (must be present and valid, otherwise row is discarded)
CRITICAL_FIELDS = {"sku", "description"}


@dataclass
class ImportRow:
    """Single row from import CSV with validation results."""
    row_number: int
    raw_data: Dict[str, str]
    mapped_data: Dict[str, Any] = field(default_factory=dict)
    is_valid: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    sku_object: Optional[SKU] = None


@dataclass
class ImportPreview:
    """Preview results from CSV parsing and validation."""
    rows: List[ImportRow]
    total_rows: int
    valid_rows: int
    discarded_rows: int
    duplicate_skus: Set[str] = field(default_factory=set)
    primary_discard_reason: str = ""
    detected_delimiter: str = ","
    column_mapping: Dict[str, str] = field(default_factory=dict)  # CSV column -> canonical field


class SKUImporter:
    """Handles SKU CSV import with validation and preview."""
    
    def __init__(self, csv_layer):
        """
        Initialize importer.
        
        Args:
            csv_layer: CSVLayer instance for persistence operations
        """
        self.csv_layer = csv_layer
    
    def auto_detect_delimiter(self, filepath: Path) -> str:
        """
        Auto-detect CSV delimiter using csv.Sniffer.
        
        Args:
            filepath: Path to CSV file
            
        Returns:
            Detected delimiter (default to ',' if detection fails)
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                sample = f.read(4096)
                sniffer = csv.Sniffer()
                delimiter = sniffer.sniff(sample).delimiter
                return delimiter
        except Exception as e:
            logger.warning(f"Delimiter detection failed, using ',': {e}")
            return ','
    
    def auto_map_columns(self, csv_headers: Sequence[str]) -> Dict[str, str]:
        """
        Auto-map CSV column names to canonical field names using aliases.
        
        Args:
            csv_headers: List of column names from CSV
            
        Returns:
            Dict mapping CSV column name -> canonical field name
        """
        mapping = {}
        csv_headers_lower = {h: h.strip() for h in csv_headers}
        
        for canonical, aliases in COLUMN_ALIASES.items():
            for header in csv_headers_lower.values():
                if header.lower().strip() in [a.lower() for a in aliases]:
                    mapping[header] = canonical
                    break
        
        return mapping
    
    def parse_csv_with_preview(
        self,
        filepath: Path,
        column_mapping: Optional[Dict[str, str]] = None,
        preview_limit: int = 50,
        encoding: str = 'utf-8'
    ) -> ImportPreview:
        """
        Parse CSV file and generate preview with validation.
        
        Args:
            filepath: Path to CSV file
            column_mapping: Optional manual column mapping (CSV col -> canonical field)
            preview_limit: Maximum rows to preview (0 = all)
            encoding: File encoding (fallback to latin-1 if utf-8 fails)
            
        Returns:
            ImportPreview with parsed and validated rows
        """
        # Try UTF-8 first, fallback to latin-1
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                delimiter = self.auto_detect_delimiter(filepath)
                reader = csv.DictReader(f, delimiter=delimiter)
                csv_headers = reader.fieldnames or []
                rows_data = list(reader)
        except UnicodeDecodeError:
            logger.warning("UTF-8 decode failed, trying latin-1")
            with open(filepath, 'r', encoding='latin-1') as f:
                delimiter = self.auto_detect_delimiter(filepath)
                reader = csv.DictReader(f, delimiter=delimiter)
                csv_headers = reader.fieldnames or []
                rows_data = list(reader)
        
        # Auto-map columns if not provided
        if column_mapping is None:
            column_mapping = self.auto_map_columns(csv_headers)
        
        # Get existing SKUs for duplicate detection
        existing_skus = {sku.sku for sku in self.csv_layer.read_skus()}
        
        # Parse and validate rows
        import_rows = []
        valid_count = 0
        discard_reasons = {}
        seen_skus_in_file = set()
        duplicate_skus = set()
        
        for idx, raw_row in enumerate(rows_data, start=2):  # Row 2 = first data row (after header)
            # Limit preview if needed
            if preview_limit > 0 and idx > preview_limit + 1:
                break
            
            import_row = ImportRow(row_number=idx, raw_data=raw_row)
            
            # Map columns
            mapped = self._map_row(raw_row, column_mapping)
            import_row.mapped_data = mapped
            
            # Validate row
            errors, warnings = self._validate_row(mapped, existing_skus, seen_skus_in_file)
            import_row.errors = errors
            import_row.warnings = warnings
            
            # Check for duplicates in file
            sku_code = mapped.get("sku", "").strip()
            if sku_code in seen_skus_in_file:
                duplicate_skus.add(sku_code)
                import_row.errors.append(f"Duplicate SKU in file: {sku_code}")
            elif sku_code:
                seen_skus_in_file.add(sku_code)
            
            # Mark as valid or discarded
            if not errors:
                import_row.is_valid = True
                import_row.sku_object = self._create_sku_object(mapped)
                valid_count += 1
            else:
                import_row.is_valid = False
                # Track discard reasons
                primary_error = errors[0]
                discard_reasons[primary_error] = discard_reasons.get(primary_error, 0) + 1
            
            import_rows.append(import_row)
        
        # Determine primary discard reason
        primary_discard_reason = ""
        if discard_reasons:
            primary_discard_reason = max(discard_reasons.items(), key=lambda x: x[1])[0]
        
        total_rows = len(rows_data)
        discarded_rows = total_rows - valid_count
        
        return ImportPreview(
            rows=import_rows,
            total_rows=total_rows,
            valid_rows=valid_count,
            discarded_rows=discarded_rows,
            duplicate_skus=duplicate_skus,
            primary_discard_reason=primary_discard_reason,
            detected_delimiter=delimiter,
            column_mapping=column_mapping
        )
    
    def _map_row(self, raw_row: Dict[str, str], column_mapping: Dict[str, str]) -> Dict[str, Any]:
        """
        Map raw CSV row to canonical field names.
        
        Args:
            raw_row: Raw CSV row dict
            column_mapping: Column mapping (CSV col -> canonical field)
            
        Returns:
            Mapped dict with canonical field names
        """
        mapped = {}
        for csv_col, canonical_field in column_mapping.items():
            if csv_col in raw_row:
                mapped[canonical_field] = raw_row[csv_col]
        return mapped
    
    def _validate_row(
        self,
        mapped_data: Dict[str, Any],
        existing_skus: Set[str],
        seen_skus_in_file: Set[str]
    ) -> Tuple[List[str], List[str]]:
        """
        Validate mapped row data.
        
        Args:
            mapped_data: Mapped row data
            existing_skus: Set of existing SKU codes in database
            seen_skus_in_file: Set of SKU codes already seen in this file
            
        Returns:
            Tuple of (errors, warnings)
        """
        errors = []
        warnings = []
        
        # Critical fields validation
        for field in CRITICAL_FIELDS:
            value = mapped_data.get(field, "").strip()
            if not value:
                errors.append(f"Missing critical field: {field}")
        
        # If critical fields missing, skip further validation
        if errors:
            return errors, warnings
        
        # Type and range validations
        try:
            # Integer fields with range checks
            int_fields = {
                "moq": (1, None),
                "pack_size": (1, None),
                "lead_time_days": (0, 365),
                "review_period": (0, None),
                "safety_stock": (0, None),
                "shelf_life_days": (0, None),
                "min_shelf_life_days": (0, None),
                "max_stock": (1, None),
                "reorder_point": (0, None),
                "mc_n_simulations": (0, None),
                "mc_random_seed": (0, None),
                "mc_output_percentile": (0, 99),
                "mc_horizon_days": (0, None),
            }
            
            for field, (min_val, max_val) in int_fields.items():
                if field in mapped_data and mapped_data[field]:
                    try:
                        val = int(mapped_data[field])
                        if min_val is not None and val < min_val:
                            errors.append(f"{field} must be >= {min_val}")
                        if max_val is not None and val > max_val:
                            errors.append(f"{field} must be <= {max_val}")
                    except ValueError:
                        errors.append(f"{field} must be an integer")
            
            # Float fields with range checks
            float_fields = {
                "waste_penalty_factor": (0.0, 1.0),
                "waste_risk_threshold": (0.0, 100.0),
                "oos_boost_percent": (0.0, 100.0),
                "target_csl": (0.0, 0.9999),
            }
            
            for field, (min_val, max_val) in float_fields.items():
                if field in mapped_data and mapped_data[field]:
                    try:
                        val = float(mapped_data[field])
                        if val < min_val or val > max_val:
                            errors.append(f"{field} must be in range [{min_val}, {max_val}]")
                    except ValueError:
                        errors.append(f"{field} must be a number")
            
            # Enum validations
            if "demand_variability" in mapped_data and mapped_data["demand_variability"]:
                var_str = mapped_data["demand_variability"].strip().upper()
                valid_vars = [v.name for v in DemandVariability]
                if var_str not in valid_vars:
                    errors.append(f"demand_variability must be one of: {', '.join(valid_vars)}")
            
            if "waste_penalty_mode" in mapped_data and mapped_data["waste_penalty_mode"]:
                mode = mapped_data["waste_penalty_mode"].strip().lower()
                if mode not in ["", "soft", "hard"]:
                    errors.append("waste_penalty_mode must be '', 'soft', or 'hard'")
            
            if "oos_detection_mode" in mapped_data and mapped_data["oos_detection_mode"]:
                mode = mapped_data["oos_detection_mode"].strip().lower()
                if mode not in ["", "strict", "relaxed"]:
                    errors.append("oos_detection_mode must be '', 'strict', or 'relaxed'")
            
            if "oos_popup_preference" in mapped_data and mapped_data["oos_popup_preference"]:
                pref = mapped_data["oos_popup_preference"].strip().lower()
                if pref not in ["ask", "always_yes", "always_no"]:
                    errors.append("oos_popup_preference must be 'ask', 'always_yes', or 'always_no'")
            
            if "forecast_method" in mapped_data and mapped_data["forecast_method"]:
                method = mapped_data["forecast_method"].strip().lower()
                if method not in ["", "simple", "monte_carlo"]:
                    errors.append("forecast_method must be '', 'simple', or 'monte_carlo'")
            
            if "mc_distribution" in mapped_data and mapped_data["mc_distribution"]:
                dist = mapped_data["mc_distribution"].strip().lower()
                if dist not in ["", "empirical", "normal", "lognormal", "residuals"]:
                    errors.append("mc_distribution must be '', 'empirical', 'normal', 'lognormal', or 'residuals'")
            
            if "mc_output_stat" in mapped_data and mapped_data["mc_output_stat"]:
                stat = mapped_data["mc_output_stat"].strip().lower()
                if stat not in ["", "mean", "percentile"]:
                    errors.append("mc_output_stat must be '', 'mean', or 'percentile'")
            
            if "mc_horizon_mode" in mapped_data and mapped_data["mc_horizon_mode"]:
                mode = mapped_data["mc_horizon_mode"].strip().lower()
                if mode not in ["", "auto", "custom"]:
                    errors.append("mc_horizon_mode must be '', 'auto', or 'custom'")
            
            # EAN validation (warning only)
            if "ean" in mapped_data and mapped_data["ean"]:
                ean = mapped_data["ean"].strip()
                if ean:
                    is_valid, error_msg = validate_ean(ean)
                    if not is_valid:
                        warnings.append(f"Invalid EAN format: {error_msg}")
            
            # Cross-field validation: min_shelf_life vs shelf_life
            shelf_life = int(mapped_data.get("shelf_life_days", 0) or 0)
            min_shelf_life = int(mapped_data.get("min_shelf_life_days", 0) or 0)
            if shelf_life > 0 and min_shelf_life > shelf_life:
                errors.append(f"min_shelf_life_days ({min_shelf_life}) cannot exceed shelf_life_days ({shelf_life})")
        
        except Exception as e:
            errors.append(f"Validation error: {str(e)}")
        
        return errors, warnings
    
    def _create_sku_object(self, mapped_data: Dict[str, Any]) -> SKU:
        """
        Create SKU object from validated mapped data.
        Uses SKU dataclass defaults for missing non-critical fields.
        
        Args:
            mapped_data: Validated mapped row data
            
        Returns:
            SKU object
        """
        # Parse demand_variability
        demand_var_str = mapped_data.get("demand_variability", "STABLE").strip().upper()
        try:
            demand_var = DemandVariability[demand_var_str]
        except KeyError:
            demand_var = DemandVariability.STABLE
        
        # Parse boolean for in_assortment
        in_assortment_str = mapped_data.get("in_assortment", "true").strip().lower()
        in_assortment = in_assortment_str in ("true", "1", "yes", "t", "")
        
        # Build SKU with defaults from dataclass
        return SKU(
            sku=mapped_data.get("sku", "").strip(),
            description=mapped_data.get("description", "").strip(),
            ean=mapped_data.get("ean", "").strip() or None,
            moq=int(mapped_data.get("moq") or 1),
            pack_size=int(mapped_data.get("pack_size") or 1),
            lead_time_days=int(mapped_data.get("lead_time_days") or 7),
            review_period=int(mapped_data.get("review_period") or 7),
            safety_stock=int(mapped_data.get("safety_stock") or 0),
            shelf_life_days=int(mapped_data.get("shelf_life_days") or 0),
            min_shelf_life_days=int(mapped_data.get("min_shelf_life_days") or 0),
            waste_penalty_mode=mapped_data.get("waste_penalty_mode", "").strip(),
            waste_penalty_factor=float(mapped_data.get("waste_penalty_factor") or 0.0),
            waste_risk_threshold=float(mapped_data.get("waste_risk_threshold") or 0.0),
            max_stock=int(mapped_data.get("max_stock") or 999),
            reorder_point=int(mapped_data.get("reorder_point") or 10),
            demand_variability=demand_var,
            category=mapped_data.get("category", "").strip(),
            department=mapped_data.get("department", "").strip(),
            oos_boost_percent=float(mapped_data.get("oos_boost_percent") or 0.0),
            oos_detection_mode=mapped_data.get("oos_detection_mode", "").strip(),
            oos_popup_preference=mapped_data.get("oos_popup_preference", "ask").strip() or "ask",
            forecast_method=mapped_data.get("forecast_method", "").strip(),
            mc_distribution=mapped_data.get("mc_distribution", "").strip(),
            mc_n_simulations=int(mapped_data.get("mc_n_simulations") or 0),
            mc_random_seed=int(mapped_data.get("mc_random_seed") or 0),
            mc_output_stat=mapped_data.get("mc_output_stat", "").strip(),
            mc_output_percentile=int(mapped_data.get("mc_output_percentile") or 0),
            mc_horizon_mode=mapped_data.get("mc_horizon_mode", "").strip(),
            mc_horizon_days=int(mapped_data.get("mc_horizon_days") or 0),
            in_assortment=in_assortment,
            target_csl=float(mapped_data.get("target_csl") or 0.0),
        )
    
    def execute_import(
        self,
        preview: ImportPreview,
        mode: str = "UPSERT",
        require_confirmation_on_discards: bool = True
    ) -> Dict[str, Any]:
        """
        Execute the import based on preview results.
        
        Args:
            preview: ImportPreview with validated rows
            mode: "UPSERT" (update existing + add new) or "REPLACE" (overwrite all)
            require_confirmation_on_discards: If True and mode is REPLACE with discards,
                                              returns {"confirmation_required": True} instead of executing
            
        Returns:
            Dict with import results:
            {
                "success": bool,
                "imported": int,
                "updated": int,
                "added": int,
                "discarded": int,
                "errors": List[str],
                "confirmation_required": bool (only if confirmation needed)
            }
        """
        result = {
            "success": False,
            "imported": 0,
            "updated": 0,
            "added": 0,
            "discarded": preview.discarded_rows,
            "errors": [],
            "confirmation_required": False
        }
        
        # Check for confirmation requirement in REPLACE mode with discards
        if mode == "REPLACE" and preview.discarded_rows > 0 and require_confirmation_on_discards:
            result["confirmation_required"] = True
            return result
        
        # Extract valid SKUs
        valid_skus = [row.sku_object for row in preview.rows if row.is_valid and row.sku_object]
        
        if not valid_skus:
            result["errors"].append("No valid SKUs to import")
            return result
        
        try:
            if mode == "UPSERT":
                # UPSERT: update existing + add new
                existing_skus = {sku.sku: sku for sku in self.csv_layer.read_skus()}
                
                for sku_obj in valid_skus:
                    if sku_obj.sku in existing_skus:
                        self.csv_layer.update_sku_object(sku_obj.sku, sku_obj)
                        result["updated"] += 1
                    else:
                        self.csv_layer.write_sku(sku_obj)
                        result["added"] += 1
                
                result["imported"] = result["updated"] + result["added"]
                result["success"] = True
            
            elif mode == "REPLACE":
                # REPLACE: overwrite entire skus.csv with valid SKUs from import
                # Convert SKU objects to dict rows
                sku_rows = []
                for sku_obj in valid_skus:
                    sku_dict = {
                        "sku": sku_obj.sku,
                        "description": sku_obj.description,
                        "ean": sku_obj.ean or "",
                        "moq": str(sku_obj.moq),
                        "pack_size": str(sku_obj.pack_size),
                        "lead_time_days": str(sku_obj.lead_time_days),
                        "review_period": str(sku_obj.review_period),
                        "safety_stock": str(sku_obj.safety_stock),
                        "shelf_life_days": str(sku_obj.shelf_life_days),
                        "min_shelf_life_days": str(sku_obj.min_shelf_life_days),
                        "waste_penalty_mode": sku_obj.waste_penalty_mode,
                        "waste_penalty_factor": str(sku_obj.waste_penalty_factor),
                        "waste_risk_threshold": str(sku_obj.waste_risk_threshold),
                        "max_stock": str(sku_obj.max_stock),
                        "reorder_point": str(sku_obj.reorder_point),
                        "demand_variability": sku_obj.demand_variability.name,
                        "category": sku_obj.category,
                        "department": sku_obj.department,
                        "oos_boost_percent": str(sku_obj.oos_boost_percent),
                        "oos_detection_mode": sku_obj.oos_detection_mode,
                        "oos_popup_preference": sku_obj.oos_popup_preference,
                        "forecast_method": sku_obj.forecast_method,
                        "mc_distribution": sku_obj.mc_distribution,
                        "mc_n_simulations": str(sku_obj.mc_n_simulations),
                        "mc_random_seed": str(sku_obj.mc_random_seed),
                        "mc_output_stat": sku_obj.mc_output_stat,
                        "mc_output_percentile": str(sku_obj.mc_output_percentile),
                        "mc_horizon_mode": sku_obj.mc_horizon_mode,
                        "mc_horizon_days": str(sku_obj.mc_horizon_days),
                        "in_assortment": "true" if sku_obj.in_assortment else "false",
                        "target_csl": str(sku_obj.target_csl),
                    }
                    sku_rows.append(sku_dict)
                
                # Write atomically with backup
                self.csv_layer._write_csv_atomic("skus.csv", sku_rows)
                result["imported"] = len(valid_skus)
                result["added"] = len(valid_skus)
                result["success"] = True
            
            else:
                result["errors"].append(f"Unknown import mode: {mode}")
        
        except Exception as e:
            result["errors"].append(f"Import failed: {str(e)}")
            logger.exception("Import execution failed")
        
        return result
    
    def export_discard_details(
        self,
        preview: ImportPreview,
        output_path: Path
    ):
        """
        Export detailed discard report to CSV.
        
        Args:
            preview: ImportPreview with validation results
            output_path: Path for output CSV file
        """
        discarded_rows = [row for row in preview.rows if not row.is_valid]
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Row Number", "SKU", "Description", "Errors", "Warnings"])
            
            for row in discarded_rows:
                sku = row.mapped_data.get("sku", "")
                desc = row.mapped_data.get("description", "")
                errors = "; ".join(row.errors)
                warnings = "; ".join(row.warnings)
                writer.writerow([row.row_number, sku, desc, errors, warnings])
        
        logger.info(f"Exported {len(discarded_rows)} discarded rows to {output_path}")
