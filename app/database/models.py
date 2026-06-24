"""
SQLAlchemy models for the calibration database.

These models map to existing SQL Server tables - we do not create/modify schema.
"""

from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Boolean, Numeric
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class OrderCalibrationMaster(Base):
    """
    Work order master table.
    
    Used to load work order context when operator enters Shop Order.
    """
    __tablename__ = 'OrderCalibrationMaster'
    
    # Primary key (composite in live SQL Server)
    ShopOrder = Column(String(30), primary_key=True)
    
    # Work order details
    PartID = Column(String(30), primary_key=True)
    WascoDescription = Column(String(60))
    LastSequenceCalibrated = Column(String(4))  # a.k.a. SequenceID
    OrderQTY = Column(Integer)
    
    # Tracking
    OperatorID = Column(String(20))
    EquipmentID = Column(String(20))
    StartTime = Column(DateTime)
    FinishTime = Column(DateTime)
    CalibrationDate = Column(DateTime)
    ModificationDate = Column(DateTime)
    TemperatureC = Column(Numeric(10, 4))
    ActivationTarget = Column(String(50))
    ActivationMaxAllowable = Column(Numeric(10, 4))
    ActivationMinAllowable = Column(Numeric(10, 4))
    CreatedBy = Column(String(20))
    CreationDate = Column(DateTime)
    ModifiedBy = Column(String(20))

    def __repr__(self):
        return f"<OrderCalibrationMaster(ShopOrder='{self.ShopOrder}', PartID='{self.PartID}')>"


class ProductTestParameters(Base):
    """
    Test parameters table (key-value structure).
    
    Key: (PartID, SequenceID, ParameterName)
    Value: ParameterValue
    """
    __tablename__ = 'ProductTestParameters'
    
    # Composite primary key
    PartID = Column(String(30), primary_key=True)
    SequenceID = Column(String(4), primary_key=True)
    ParameterName = Column(String(40), primary_key=True)
    
    # Value
    ParameterValue = Column(String(200))
    
    def __repr__(self):
        return f"<PTP({self.PartID}/{self.SequenceID}/{self.ParameterName}={self.ParameterValue})>"


class OrderCalibrationDetail(Base):
    """
    Test results table.
    
    One row per unit tested. Retest updates the same row.
    """
    __tablename__ = 'OrderCalibrationDetail'
    
    # Composite primary key
    ShopOrder = Column(String(30), primary_key=True)
    SequenceID = Column(String(4), primary_key=True)
    PartID = Column(String(30), primary_key=True)
    SerialNumber = Column(Integer, primary_key=True)
    ActivationID = Column(Integer, primary_key=True, default=1)
    
    # Measurements (direction-based)
    IncreasingActivation = Column(Numeric(10, 4))   # Switching point while pressure increasing
    DecreasingDeactivation = Column(Numeric(10, 4)) # Switching point while pressure decreasing
    TemperatureC = Column(Numeric(10, 4))
    IncreasingGap = Column(Numeric(10, 4), default=0)
    DecreasingGap = Column(Numeric(10, 4), default=0)
    MaxPressureAchieved = Column(Numeric(10, 4))
    
    # Evaluation
    InSpec = Column(Boolean)  # Overall pass/fail
    
    # Units
    UnitsOfMeasure = Column(String(20))
    GageReferenceDiff = Column(Numeric(10, 4))
    
    # Tracking
    InspectionDate = Column(DateTime, default=datetime.now)
    OperatorID = Column(String(20))
    EquipmentID = Column(String(20))
    
    def __repr__(self):
        return (f"<OrderCalibrationDetail(ShopOrder='{self.ShopOrder}', "
                f"SerialNumber={self.SerialNumber}, InSpec={self.InSpec})>")
