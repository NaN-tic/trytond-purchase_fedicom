# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
import socket
import logging
from trytond.modules.sale_fedicom.service.messages.init_session \
    import InitSession
from trytond.modules.sale_fedicom.service.messages.close_session \
    import CloseSession
from trytond.modules.sale_fedicom.service.messages.order import Order
from trytond.modules.sale_fedicom.service.messages.order_line import OrderLine
from trytond.modules.sale_fedicom.service.messages.finish_order \
    import FinishOrder
from trytond.model import fields
from trytond.pool import PoolMeta

__all__ = ['Party', 'Product', 'Purchase']


class Party:
    __name__ = 'party.party'
    __metaclass__ = PoolMeta

    host = fields.Char('Host')
    port = fields.Integer('Port')
    timeout = fields.Integer('Timeout')


class Product:
    __name__ = 'product.product'
    __metaclass__ = PoolMeta

    supplier_code = fields.Function(fields.Char('Supplier Code'),
        'get_supplier_code')

    def get_supplier_code(self, name):
        if self.template.product_suppliers:
            return self.template.product_suppliers[0].code


class Purchase:
    __name__ = 'purchase.purchase'
    __metaclass__ = PoolMeta

    @classmethod
    def process(cls, purchases):
        for purchase in purchases:
            purchase.process_fedicom_purchase()
        super(Purchase, cls).process(purchases)

    def process_fedicom_purchase(self):
        logger = logging.getLogger('purchase_fedicom')

        logger.info('Process Purchase %s From Party %s' % (
            self.reference, self.party.code))

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((self.party.host, self.party.port))

        msg = self.send_order()
        sock.sendall(msg)
        data = sock.recv(2048)
        sock.close()

        data_list = data.split('\r\n')

        for msg in data_list:
            print msg

    def send_order(self):
        msg = ""
        msg += str(InitSession(self.party.fedicom_user,
            self.party.fedicom_password, ''))
        msg += str(Order(self.party.fedicom_user, '1'))
        quantity = 0
        for line in self.lines:
            msg += str(OrderLine(
                line.product.supplier_code, int(line.quantity)))
            quantity += line.quantity
        f = FinishOrder()
        f.finishOrder(str(len(self.lines)), int(quantity), 0)
        msg += str(f)
        msg += str(CloseSession())
        return msg
