package admission

import "errors"

// ErrNotAdaptive is returned when ApplyLimit is called on a strategy
// that does not accept external limit changes (e.g. static baseline).
var ErrNotAdaptive = errors.New("admission: strategy does not accept external limits")
