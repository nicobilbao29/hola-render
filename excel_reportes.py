"""Exportación XLSX de la aplicación con la biblioteca estándar de Python.

Identificadores como texto, importes numéricos y fórmulas con valor en caché.
No ejecuta texto de productos como fórmulas ni consulta servicios externos.
"""
import io
import re
import zipfile
from math import ceil
from datetime import date
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
MIME='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def cell(row, ref, value, style=0, formula=None):
    c=SubElement(row,'c',r=ref,s=str(style))
    if formula is not None:SubElement(c,'f').text=formula
    if value is None:return c
    if isinstance(value,date):value=(value-date(1899,12,30)).days
    if isinstance(value,(int,Decimal)):
        SubElement(c,'v').text=str(value)
    elif formula is not None:
        c.set('t','str');SubElement(c,'v').text=str(value)
    else:
        c.set('t','inlineStr')
        text=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]','',str(value))[:32767]
        SubElement(SubElement(c,'is'),'t',{'xml:space':'preserve'}).text=text
    return c


def sheet(title,headers,widths,start,end,seller):
    root=Element('worksheet',xmlns=NS)
    view=SubElement(SubElement(root,'sheetViews'),'sheetView',workbookViewId='0')
    SubElement(view,'pane',ySplit='6',topLeftCell='A7',activePane='bottomLeft',state='frozen')
    SubElement(root,'sheetFormatPr',defaultRowHeight='20')
    cols=SubElement(root,'cols')
    for i,width in enumerate(widths,1):SubElement(cols,'col',min=str(i),max=str(i),width=str(width),customWidth='1')
    data=SubElement(root,'sheetData')
    cell(SubElement(data,'row',r='1',ht='30',customHeight='1'),'A1',title,1)
    cell(SubElement(data,'row',r='2',ht='44',customHeight='1'),'A2','Fuente: ventas y cargos importados de Mercado Libre; costos ingresados por ti. Pendiente no significa cero. CLP.',5)
    r=SubElement(data,'row',r='3');cell(r,'A3','Desde');cell(r,'B3',date.fromisoformat(start),4);cell(r,'C3',date.fromisoformat(end),4)
    cell(SubElement(data,'row',r='4'),'A4','Vendedor: '+str(seller or 'sin conectar'))
    row=SubElement(data,'row',r='6',ht='36',customHeight='1')
    for i,label in enumerate(headers):cell(row,chr(65+i)+'6',label,2)
    return root,data


STYLES='''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0.00;[Red](&quot;$&quot;#,##0.00)"/><numFmt numFmtId="165" formatCode="dd/mm/yyyy"/></numFmts>
<fonts count="3"><font><sz val="11"/><name val="Calibri"/><color rgb="FF26362C"/></font><font><b/><sz val="18"/><name val="Calibri"/><color rgb="FF26362C"/></font><font><b/><sz val="11"/><name val="Calibri"/><color rgb="FFFFFFFF"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF26362C"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment vertical="top"/></xf><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0"><alignment wrapText="1" vertical="center"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment wrapText="1" vertical="top"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''


def build_excel(report,start,end,seller):
    """Las cifras y filas son las mismas del dashboard; no limita a las 200 visibles."""
    pesos=lambda n:None if n is None else Decimal(n)/100
    headers=['Primer número','Identificador de envío','Fecha Chile','Estado ML','Productos','Venta total CLP','Comisión ML CLP','Despacho CLP','Costo mercadería CLP','Ganancia CLP','Datos pendientes','Modalidad']
    detail,data=sheet('Ventas y ganancia',headers,[25,25,15,16,55,20,20,20,22,20,44,22],start,end,seller)
    sales=report['sale_details']
    for i,s in enumerate(sales,7):
        row=SubElement(data,'row',r=str(i),ht='36',customHeight='1')
        missing=[]
        if s['comision'] is None:missing.append('Comisión ML')
        if s['despacho'] is None:missing.append('Despacho')
        if s['unidades_sin_costo']:missing.append('Costo mercadería')
        values=[str(s['id']),str(s['envio_id'] or ''),date.fromisoformat(s['fecha']),s['estado'],
            ' / '.join(l['nombre']+' × '+str(l['cantidad']) for l in s['lineas']),pesos(s['total']),pesos(s['comision']),pesos(s['despacho']),
            None if s['unidades_sin_costo'] else pesos(s['costo_producto']),pesos(s['ganancia_venta']) if not missing else 'Pendiente',
            ', '.join(missing) if missing else 'Completo',s['tipo']]
        row.set('ht',str(min(409,max(36,16*max(ceil(len(values[4])/48),ceil(len(values[10])/38))+6))))
        for j,value in enumerate(values):
            formula=f'IF(COUNT(F{i}:I{i})=4,F{i}-G{i}-H{i}-I{i},"Pendiente")' if j==9 else None
            cell(row,chr(65+j)+str(i),value,4 if j==2 else (3 if 5<=j<=9 else (5 if j in (4,10) else 0)),formula)
    last=max(7,len(sales)+6)
    SubElement(detail,'autoFilter',ref=f'A6:L{max(6,len(sales)+6)}')
    merge=SubElement(detail,'mergeCells',count='2')
    for ref in ('A1:L1','A2:L2'):SubElement(merge,'mergeCell',ref=ref)
    summary,sd=sheet('Resumen del dashboard',['Concepto','Importe CLP','Estado / detalle'],[42,24,78],start,end,seller)
    ranges={c:f"'Ventas'!{c}7:{c}{last}" for c in 'DFGHI'}
    records=[('Venta total',report['gross'],False,'F','Solo órdenes pagadas'),
        ('Comisión Mercado Libre',report['fees'],bool(report['missing_fees']),'G','Cargo por venta'),
        ('Cargo por despacho',report['shipping'],bool(report['missing_dispatch']),'H','Tu cargo ML y reparto Flex'),
        ('Costo de mercadería',report['costs'],bool(report['missing_cost']),'I','Costo asignado × unidades'),
        ('Ganancia de las ventas',report['profit'],report['profit'] is None,None,'Venta − comisión ML − despacho − mercadería')]
    for i,(label,amount,pending,col,note) in enumerate(records,7):
        row=SubElement(sd,'row',r=str(i));cell(row,'A'+str(i),label)
        if col:
            total=f'SUMIF({ranges["D"]},"paid",{ranges[col]})'
            formula=total if col=='F' else f'IF(COUNTIFS({ranges["D"]},"paid",{ranges[col]},"")>0,"Pendiente",{total})'
        else:formula='IF(COUNT(B7:B10)=4,B7-B8-B9-B10,"Pendiente")'
        cell(row,'B'+str(i),'Pendiente' if pending else pesos(amount),3,formula)
        cell(row,'C'+str(i),('Faltan datos. ' if pending else '')+note,5)
    notes=[('Gastos generales registrados',pesos(report['expense']),'Se muestran aparte de la ganancia de las ventas.'),
        ('Bonificaciones Flex registradas',pesos(report['flex_credit']),'No se suman a la fórmula principal.'),
        ('Alcance','Antes de impuestos','Revisa devoluciones, descuentos y ajustes posteriores. No certifica depósitos de Mercado Pago.'),
        ('Identificadores','Primer número = orden importada','El identificador de envío es el shipping.id asociado; ambos se conservan como texto.'),
        ('Actualización','Archivo descargado','Editar Excel no cambia los costos ni las ventas del catálogo.')]
    for i,values in enumerate(notes,14):
        row=SubElement(sd,'row',r=str(i),ht='38',customHeight='1')
        for j,value in enumerate(values):cell(row,chr(65+j)+str(i),value,3 if isinstance(value,Decimal) else 5)
    merge=SubElement(summary,'mergeCells',count='2')
    for ref in ('A1:C1','A2:C2'):SubElement(merge,'mergeCell',ref=ref)
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''')
        z.writestr('_rels/.rels',f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets><sheet name="Resumen" sheetId="1" r:id="rId1"/><sheet name="Ventas" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels',f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{REL}/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="{REL}/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml',STYLES)
        for i,root in enumerate((summary,detail),1):z.writestr(f'xl/worksheets/sheet{i}.xml',tostring(root,encoding='utf-8',xml_declaration=True))
    return buffer.getvalue()
