package workload

import "strings"

// Class identifies a stable, version-controlled workload class.
// The set of classes is part of the experimental protocol and must not
// change silently .
type Class string

const (
	ClassSimple      Class = "simple"
	ClassMedium      Class = "medium"
	ClassComplex     Class = "complex"
	ClassAggregation Class = "aggregation"
)

// AllClasses returns every supported workload class.
func AllClasses() []Class {
	return []Class{ClassSimple, ClassMedium, ClassComplex, ClassAggregation}
}

// ParseClass converts a raw identifier into a Class.
func ParseClass(raw string) (Class, bool) {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case string(ClassSimple):
		return ClassSimple, true
	case string(ClassMedium):
		return ClassMedium, true
	case string(ClassComplex):
		return ClassComplex, true
	case string(ClassAggregation):
		return ClassAggregation, true
	default:
		return "", false
	}
}

// Valid reports whether c is a registered workload class.
func (c Class) Valid() bool {
	for _, known := range AllClasses() {
		if c == known {
			return true
		}
	}
	return false
}

// Product is the domain representation of a catalog product.
type Product struct {
	ID        int64   `json:"id"`
	Name      string  `json:"name"`
	Price     float64 `json:"price"`
	Category  string  `json:"category"`
	CreatedAt string  `json:"created_at"`
}

// OrderSummary aggregates an order's computed total.
type OrderSummary struct {
	ID         int64   `json:"id"`
	CustomerID int64   `json:"customer_id"`
	Total      float64 `json:"total"`
}

// OrderItemDetail joins order items with product information.
type OrderItemDetail struct {
	ID          int64   `json:"id"`
	Quantity    int32   `json:"quantity"`
	Price       float64 `json:"price"`
	ProductID   int64   `json:"product_id"`
	ProductName string  `json:"product_name"`
	Category    string  `json:"category"`
}

// OrderComplex is the result of a multi-join analytical query for one order.
type OrderComplex struct {
	OrderID       int64             `json:"order_id"`
	CustomerID    int64             `json:"customer_id"`
	Total         float64           `json:"total"`
	ItemCount     int64             `json:"item_count"`
	TotalQuantity int64             `json:"total_quantity"`
	MaxItemPrice  float64           `json:"max_item_price"`
	MinItemPrice  float64           `json:"min_item_price"`
	AvgLineValue  float64           `json:"avg_line_value"`
	Items         []OrderItemDetail `json:"items,omitempty"`
}

// CategoryRevenue is one row of the analytical aggregation workload.
type CategoryRevenue struct {
	Category      string  `json:"category"`
	ProductCount  int64   `json:"product_count"`
	SoldItems     int64   `json:"sold_items"`
	TotalQuantity int64   `json:"total_quantity"`
	AvgPrice      float64 `json:"avg_price"`
	Revenue       float64 `json:"revenue"`
}
