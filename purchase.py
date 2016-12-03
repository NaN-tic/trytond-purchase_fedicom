# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
import sys
import traceback
import logging
from decimal import Decimal
from trytond.modules.sale_fedicom.service.nan_socket import Socket
from trytond.modules.sale_fedicom.service.messages.init_session \
    import InitSession
from trytond.modules.sale_fedicom.service.messages.close_session \
    import CloseSession
from trytond.modules.sale_fedicom.service.messages.order import Order
from trytond.modules.sale_fedicom.service.messages.order_line import OrderLine
from trytond.modules.sale_fedicom.service.messages.finish_order \
    import FinishOrder
from trytond.modules.sale_fedicom.service.messages.incidence_header \
    import IncidenceHeader
from trytond.modules.sale_fedicom.service.messages.incidence_order_line \
    import IncidenceOrderLine
from trytond.model import fields
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction

__all__ = ['Party', 'Product', 'Purchase', 'FedicomLog']

_ZERO = Decimal(0)


class Party:
    __name__ = 'party.party'
    __metaclass__ = PoolMeta

    fedicom_host = fields.Char('Fedicom Host')
    fedicom_port = fields.Integer('Fedicom Port')
    fedicom_timeout = fields.Integer('Fedicom Timeout')


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
    def __setup__(cls):
        super(Purchase, cls).__setup__()
        cls._error_messages.update({
            'error_connecting': 'Error connecting to fedicom server.',
            'wrong_frame': 'frame sent does not belong to next state.',
            })

    @classmethod
    def process(cls, purchases):
        for purchase in purchases:
            purchase.process_fedicom_purchase()
        super(Purchase, cls).process(purchases)

    def process_fedicom_purchase(self):
        FedicomLog = Pool().get('fedicom.log')
        logger = logging.getLogger('purchase_fedicom')

        logger.info('Process Purchase %s From Party %s' % (
            self.reference, self.party.code))

        if not self.party.fedicom_host or not self.party.fedicom_port:
            return

        sock = Socket()
        sock.socket.settimeout(self.party.fedicom_timeout or 30)
        try:
            sock.connect(self.party.fedicom_host, self.party.fedicom_port)
        except:
            exc_type, exc_value = sys.exc_info()[:2]
            logger.warning("Exception connecting to fedicom server: "
                    "%s (%s)\n  %s"
                % (exc_type, exc_value, traceback.format_exc()))
            with Transaction().set_user(0):
                with Transaction().new_cursor():
                    FedicomLog.create([{
                        'message': self.raise_user_error('error_connecting',
                            raise_exception=False),
                        'party': self.party.id,
                        'purchase': self.id,
                    }])
                    Transaction().cursor.commit()
            self.raise_user_error('error_connecting')

        msg = self.send_order()
        sock.send(msg)
        data = sock.recieve()
        sock.disconnect()

        self.process_message(data)

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

    def process_message(self, msg):
        logger = logging.getLogger('purchase_fedicom')
        logger.info('Process Order Incidence')

        msg_list = msg.split('\r\n')
        i = 0

        init_session = InitSession()
        init_session.set_message(msg_list[i])

        i = i + 1
        next_message = init_session.next_state()
        incidence = {}
        incidence_lines = {}
        while i < len(msg_list) - 1:
            msg = msg_list[i]
            if not msg[0:4] in next_message:
                logger.warning("An error has occurred, "
                    "frame sent does not belong to next state")
                with Transaction().set_user(0):
                    with Transaction().new_cursor():
                        FedicomLog.create([{
                            'message': self.raise_user_error('wrong_frame',
                                raise_exception=False),
                            'party': self.party.id,
                            'purchase': self.id,
                        }])
                        Transaction().cursor.commit()
                self.raise_user_error('wrong_frame')

            for state in next_message:
                if msg.startswith(state):
                    if msg.startswith('0199'):
                        logger.info('Processing Close Session')
                        next_message = None
                        if incidence_lines:
                            self.process_incidence(incidence_lines)
                        return
                    elif msg.startswith('2010'):
                        logger.info('Processing Incidence Header')
                        incidence = IncidenceHeader()
                        incidence.set_msg(msg)
                        next_message = incidence.next_state()
                    elif msg.startswith('2015'):
                        logger.info('Processing Incidence Order Line')
                        incidence_line = IncidenceOrderLine()
                        incidence_line.set_msg(msg)
                        next_message = incidence_line.next_state()
                        product_code = incidence_line.article_code[-7:]
                        incidence_lines[product_code] = \
                            int(incidence_line.amount_not_served)
                    else:
                        with Transaction().set_user(0):
                            with Transaction().new_cursor():
                                FedicomLog.create([{
                                    'message': self.raise_user_error(
                                        'wrong_frame',
                                        raise_exception=False),
                                    'party': self.party.id,
                                    'purchase': self.id,
                                }])
                                Transaction().cursor.commit()
                        self.raise_user_error('wrong_frame')

            i = i + 1
        return

    def process_incidence(self, incidence_lines):
        pool = Pool()
        Purchase = pool.get('purchase.purchase')
        PurchaseLine = pool.get('purchase.line')
        logger = logging.getLogger('purchase_fedicom')

        purchase, = Purchase.copy([self])

        # Update purchase quantities
        amount = Decimal('0.0')
        for line in self.lines:
            if incidence_lines.get(line.product.supplier_code, 0) > 0:
                line.quantity = (line.quantity -
                    incidence_lines[line.product.supplier_code])
                line.save()
            else:
                logger.info("No result por product %s" %
                    line.product.supplier_code)
            amount += line.amount if line.type == 'line' else _ZERO

        # Update cached fields
        self.untaxed_amount_cache = amount
        self.tax_amount_cache = self.get_tax_amount()
        self.total_amount_cache = (
            self.untaxed_amount_cache + self.tax_amount_cache)
        self.save()

        # Create new purchase for missing quantities
        lines_to_delete = []
        for line in purchase.lines:
            if incidence_lines.get(line.product.supplier_code, 0) > 0:
                line.quantity = incidence_lines[line.product.supplier_code]
                line.save()
            else:
                lines_to_delete.append(line)
        PurchaseLine.delete(lines_to_delete)


class FedicomLog:
    __name__ = 'fedicom.log'
    __metaclass__ = PoolMeta

    purchase = fields.Many2One('purchase.purchase', 'Purchase')
