import os
import tempfile
from flask import Flask, request, render_template, send_file, jsonify
import pdfplumber
import re
import xml.etree.ElementTree as ET
import xml.dom.minidom

app = Flask(__name__)
app.secret_key = 'secret-key-for-session'
UPLOAD_FOLDER = tempfile.gettempdir()
ALLOWED_EXTENSIONS = {'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_fields_and_items(pdf_path):
    fields = {}
    items = []
    with pdfplumber.open(pdf_path) as pdf:
        text = ''
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + '\n'
    # 发票号码
    m = re.search(r'统一发票监制\s*([0-9]+)', text)
    fields['InvoiceNumber'] = m.group(1) if m else ''
    # 开票日期
    m = re.search(r'国家税务总局\s*([0-9]{4}年[0-9]{2}月[0-9]{2}日)', text)
    fields['IssueTime'] = m.group(1) if m else ''
    # 买方/卖方名称
    m = re.search(r'\n([\u4e00-\u9fa5A-Za-z0-9（）\(\)]+)\s+([\u4e00-\u9fa5A-Za-z0-9（）\(\)]+)\n([0-9A-Za-z]+)\s+([0-9A-Za-z]+)\n', text)
    if m:
        fields['BuyerName'] = m.group(1)
        fields['SellerName'] = m.group(2)
        fields['BuyerIdNum'] = m.group(3)
        fields['SellerIdNum'] = m.group(4)
    else:
        fields['BuyerName'] = ''
        fields['SellerName'] = ''
        fields['BuyerIdNum'] = ''
        fields['SellerIdNum'] = ''
    # 明细（多行）
    item_lines = re.findall(r'(\*[^\n]+?\d+\.\d+\s+\d+\.\d+\s+[0-9]+%\s+[0-9]+\.[0-9]+)', text)
    for line in item_lines:
        parts = re.split(r'\s+', line)
        if len(parts) >= 7:
            items.append({
                'ItemName': parts[0],
                'MeaUnits': parts[1],
                'Quantity': parts[2],
                'UnPrice': parts[3],
                'Amount': parts[4],
                'TaxRate': parts[5],
                'ComTaxAm': parts[6]
            })
    # 合计金额
    m = re.search(r'合 计 ¥([0-9.]+) ¥([0-9.]+)', text)
    fields['TotalAmWithoutTax'] = m.group(1) if m else ''
    fields['TotalTaxAm'] = m.group(2) if m else ''
    # 价税合计（大写）
    m = re.search(r'价税合计（大写）(.+?)（小写）', text)
    fields['TotalTax-includedAmountInChinese'] = m.group(1).strip() if m else ''
    # 价税合计（小写）
    m = re.search(r'（小写） ¥ ?([0-9.]+)', text)
    fields['TotalTax-includedAmount'] = m.group(1) if m else ''
    # 备注
    m = re.search(r'备\n注\n([\s\S]+?)\n开票人', text)
    fields['Remark'] = m.group(1).strip() if m else ''
    # 开票人
    m = re.search(r'开票人：(.+)', text)
    fields['Drawer'] = m.group(1).strip() if m else ''
    return fields, items

def build_xml(fields, items):
    root = ET.Element('EInvoice')
    header = ET.SubElement(root, 'Header')
    ET.SubElement(header, 'EIid').text = fields.get('InvoiceNumber', '')
    ET.SubElement(header, 'EInvoiceTag').text = 'SWEI3100'
    ET.SubElement(header, 'Version').text = '0.35'
    inherent = ET.SubElement(header, 'InherentLabel')
    in_issu = ET.SubElement(inherent, 'InIssuType')
    ET.SubElement(in_issu, 'LabelCode').text = 'Y'
    ET.SubElement(in_issu, 'LabelName').text = '是否蓝字发票标志'
    einvoice_type = ET.SubElement(inherent, 'EInvoiceType')
    ET.SubElement(einvoice_type, 'LabelCode').text = '01'
    ET.SubElement(einvoice_type, 'LabelName').text = '电子发票'
    vat = ET.SubElement(inherent, 'GeneralOrSpecialVAT')
    ET.SubElement(vat, 'LabelCode').text = '02'
    ET.SubElement(vat, 'LabelName').text = '普通发票'
    taxpayer = ET.SubElement(inherent, 'TaxpayerType')
    ET.SubElement(taxpayer, 'LabelCode').text = '5'
    ET.SubElement(taxpayer, 'LabelName').text = '自然人'
    undefined = ET.SubElement(header, 'UndefinedLabel')
    label1 = ET.SubElement(undefined, 'Label')
    ET.SubElement(label1, 'LabelType').text = '发票开具方式标签'
    ET.SubElement(label1, 'LabelCode').text = '6'
    ET.SubElement(label1, 'LabelName').text = '离线开票'
    label2 = ET.SubElement(undefined, 'Label')
    ET.SubElement(label2, 'LabelType').text = '特定征税方式标签'
    ET.SubElement(label2, 'LabelCode').text = '01'
    ET.SubElement(label2, 'LabelName').text = '不征税'
    label3 = ET.SubElement(undefined, 'Label')
    ET.SubElement(label3, 'LabelType').text = '纳税人信用等级标签'
    ET.SubElement(label3, 'LabelCode').text = 'A'
    ET.SubElement(label3, 'LabelName').text = 'A级'
    # EInvoiceData
    data = ET.SubElement(root, 'EInvoiceData')
    seller = ET.SubElement(data, 'SellerInformation')
    ET.SubElement(seller, 'SellerIdNum').text = fields.get('SellerIdNum', '')
    ET.SubElement(seller, 'SellerName').text = fields.get('SellerName', '')
    ET.SubElement(seller, 'SellerAddr').text = ''
    ET.SubElement(seller, 'SellerTelNum').text = ''
    ET.SubElement(seller, 'SellerBankName').text = ''
    ET.SubElement(seller, 'SellerBankAccNum').text = ''
    buyer = ET.SubElement(data, 'BuyerInformation')
    ET.SubElement(buyer, 'BuyerIdNum').text = fields.get('BuyerIdNum', '')
    ET.SubElement(buyer, 'BuyerName').text = fields.get('BuyerName', '')
    ET.SubElement(buyer, 'BuyerTelNum').text = ''
    ET.SubElement(buyer, 'BuyerAddr').text = ''
    ET.SubElement(buyer, 'BuyerBankName').text = ''
    ET.SubElement(buyer, 'BuyerBankAccNum').text = ''
    ET.SubElement(buyer, 'BuyerHandlingName').text = ''
    basic = ET.SubElement(data, 'BasicInformation')
    ET.SubElement(basic, 'TotalAmWithoutTax').text = fields.get('TotalAmWithoutTax', '')
    ET.SubElement(basic, 'TotalTaxAm').text = fields.get('TotalTaxAm', '')
    ET.SubElement(basic, 'TotalTax-includedAmount').text = fields.get('TotalTax-includedAmount', '')
    ET.SubElement(basic, 'TotalTax-includedAmountInChinese').text = fields.get('TotalTax-includedAmountInChinese', '')
    ET.SubElement(basic, 'Drawer').text = fields.get('Drawer', '')
    ET.SubElement(basic, 'RequestTime').text = fields.get('IssueTime', '') + ' 00:00:00'
    # 多行明细
    for item in items:
        item_elem = ET.SubElement(data, 'IssuItemInformation')
        ET.SubElement(item_elem, 'ItemName').text = item.get('ItemName', '')
        ET.SubElement(item_elem, 'SpecMod').text = ''
        ET.SubElement(item_elem, 'MeaUnits').text = item.get('MeaUnits', '')
        ET.SubElement(item_elem, 'Quantity').text = item.get('Quantity', '')
        ET.SubElement(item_elem, 'UnPrice').text = item.get('UnPrice', '')
        ET.SubElement(item_elem, 'Amount').text = item.get('Amount', '')
        ET.SubElement(item_elem, 'TaxRate').text = item.get('TaxRate', '')
        ET.SubElement(item_elem, 'ComTaxAm').text = item.get('ComTaxAm', '')
        ET.SubElement(item_elem, 'TotaltaxIncludedAmount').text = item.get('Amount', '')
        ET.SubElement(item_elem, 'TaxClassificationCode').text = '3030000000000000000'
    ET.SubElement(data, 'SpecificInformation')
    addi = ET.SubElement(data, 'AdditionalInformation')
    ET.SubElement(addi, 'Remark').text = fields.get('Remark', '')
    trans = ET.SubElement(addi, 'Transaction')
    record = ET.SubElement(trans, 'TransactionRecord')
    ET.SubElement(record, 'PaymentChannel').text = ''
    ET.SubElement(record, 'TransactionNumber').text = ''
    # TaxSupervisionInfo
    tax = ET.SubElement(root, 'TaxSupervisionInfo')
    ET.SubElement(tax, 'InvoiceNumber').text = fields.get('InvoiceNumber', '')
    ET.SubElement(tax, 'IssueTime').text = fields.get('IssueTime', '') + ' 00:00:00'
    ET.SubElement(tax, 'TaxBureauCode').text = '13100000000'
    ET.SubElement(tax, 'TaxBureauName').text = '国家税务总局上海市税务局'
    ET.SubElement(root, 'ptbh')
    return root

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400
    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'error': '请选择PDF文件'}), 400
    pdf_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(pdf_path)
    try:
        fields, items = extract_fields_and_items(pdf_path)
        xml_root = build_xml(fields, items)
        xml_str = ET.tostring(xml_root, encoding='utf-8')
        xml_pretty = xml.dom.minidom.parseString(xml_str).toprettyxml(indent='  ', encoding='utf-8')
        xml_filename = file.filename.rsplit('.', 1)[0] + '.xml'
        xml_path = os.path.join(UPLOAD_FOLDER, xml_filename)
        with open(xml_path, 'wb') as f:
            f.write(xml_pretty)
        return send_file(xml_path, as_attachment=True, download_name=xml_filename)
    except Exception as e:
        return jsonify({'error': '解析失败: ' + str(e)}), 500

@app.route('/parse', methods=['POST'])
def parse():
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400
    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'error': '请选择PDF文件'}), 400
    pdf_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(pdf_path)
    try:
        fields, items = extract_fields_and_items(pdf_path)
        return jsonify({
            'fields': fields,
            'items': items
        })
    except Exception as e:
        return jsonify({'error': '解析失败: ' + str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True) 