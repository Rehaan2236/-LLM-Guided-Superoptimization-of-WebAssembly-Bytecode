(module
  (func (export "f") (param i32 i32) (result i32)
    local.get 0
    local.get 1
    i32.xor
    i32.eqz))
