<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Compras</title>
  <style>
    body { font-family: Arial; padding: 16px; background:#f5f5f5; }
    .card { background:white; padding:16px; border-radius:12px; margin-bottom:16px; box-shadow:0 4px 10px rgba(0,0,0,0.08);}    
    input, select { width:100%; padding:10px; margin-top:6px; margin-bottom:12px; border-radius:8px; border:1px solid #ddd; }
    button { width:100%; padding:12px; border:none; border-radius:10px; background:#e63946; color:white; font-weight:bold; }
    .error { background:#ffdddd; padding:10px; border-radius:8px; margin-bottom:10px; }
    .success { background:#ddffdd; padding:10px; border-radius:8px; margin-bottom:10px; }
  </style>
</head>
<body>

<div class="card">
  <h2>Registrar compra</h2>

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      {% for category, message in messages %}
        <div class="{{ category }}">{{ message }}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}

  <form method="POST">

    <label>Fecha</label>
    <input type="date" name="fecha" 
      value="{{ form_data.fecha if form_data else '' }}" required>

    <label>Lugar</label>
    <input type="text" name="lugar" 
      value="{{ form_data.lugar if form_data else '' }}" required>

    <label>Concepto</label>
    <input type="text" name="concepto" 
      value="{{ form_data.concepto if form_data else '' }}" required>

    <label>Costo total</label>
    <input type="number" step="0.01" name="costo" 
      value="{{ form_data.costo if form_data else '' }}" required>

    <label>Cantidad</label>
    <input type="number" step="0.01" name="cantidad" 
      value="{{ form_data.cantidad if form_data else '' }}" required>

    <label>Unidad</label>
    <input type="text" name="unidad" 
      value="{{ form_data.unidad if form_data else '' }}" required>

    <label>Tipo de costo</label>
    <select name="tipo_costo">
      <option value="variable" {% if form_data and form_data.tipo_costo == 'variable' %}selected{% endif %}>Variable</option>
      <option value="fijo" {% if form_data and form_data.tipo_costo == 'fijo' %}selected{% endif %}>Fijo</option>
    </select>

    <label>¿Es insumo?</label>
    <select name="es_insumo">
      <option value="0" {% if form_data and form_data.es_insumo == '0' %}selected{% endif %}>No</option>
      <option value="1" {% if form_data and form_data.es_insumo == '1' %}selected{% endif %}>Sí</option>
    </select>

    <label>Insumo</label>
    <select name="insumo_id">
      <option value="">Seleccionar</option>
      {% for i in insumos %}
        <option value="{{ i.id }}"
          {% if form_data and form_data.insumo_id == i.id|string %}selected{% endif %}>
          {{ i.nombre }}
        </option>
      {% endfor %}
    </select>

    <label>Nota</label>
    <input type="text" name="nota" 
      value="{{ form_data.nota if form_data else '' }}">

    <button type="submit">Guardar compra</button>

  </form>
</div>

<div class="card">
  <h3>Últimas compras</h3>
  <ul>
    {% for c in compras %}
      <li>
        {{ c.fecha }} - {{ c.concepto }} - ${{ c.costo }}
      </li>
    {% endfor %}
  </ul>
</div>

</body>
</html>
